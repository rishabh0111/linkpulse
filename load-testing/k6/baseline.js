// The load baseline: what "normal" looks like, so that every chaos experiment's numbers
// have something to be compared against.
//
// Two scenarios run together, shaped like the product's real traffic mix -- many
// redirects (the hot path, the thing a user waits for) and a few GraphQL calls (link
// creation and the owner's own dashboard). Both are open-model (constant arrival rate):
// k6 keeps sending at the target rate whether or not responses come back, so a slow
// service shows up as latency and errors rather than as a quietly lower request count,
// which is what a closed-model VU loop would report.
//
// Thresholds are the pass/fail contract and they mirror the alert rules: the redirect p99
// must sit under the 250 ms LinkpulseRedirectLatencyHigh line with margin, and the error
// ratio under the 5% LinkpulseHighErrorRate line by an order of magnitude. A baseline that
// merely reports numbers is a benchmark; one that fails is a regression test.
//
// Runs from the k6 compose service against the k3d ingress:
//
//   mise run k6-baseline
//   docker compose run --rm k6 run load-testing/k6/baseline.js --summary-export load-testing/k6/output/baseline.json
//
// or against the burst cluster with BASE_URL=https://<alb-hostname>. The rates are
// deliberately modest -- 20 redirects/s is ~40 WCU/s of click writes, twice the real
// table's 20 WCU, so on the burst this baseline is ALSO the point where throttling starts,
// and the click-drop threshold below is what would catch it. Locally LocalStack enforces
// nothing (docker-compose.yml explains), so the same run is pure latency baseline.

import http from "k6/http";
import { check } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE = (__ENV.BASE_URL || "http://k3d-linkpulse-serverlb").replace(/\/$/, "");
const REDIRECT_RPS = Number(__ENV.REDIRECT_RPS || 20);
const GRAPHQL_RPS = Number(__ENV.GRAPHQL_RPS || 2);
const DURATION = __ENV.DURATION || "2m";
const LINKS = Number(__ENV.LINKS || 25);

const redirectLatency = new Trend("linkpulse_redirect_duration", true);
const graphqlLatency = new Trend("linkpulse_graphql_duration", true);
const redirectsNot302 = new Counter("linkpulse_redirect_not_302");

export const options = {
  scenarios: {
    redirects: {
      executor: "constant-arrival-rate",
      rate: REDIRECT_RPS,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 100,
      exec: "redirect",
    },
    graphql: {
      executor: "constant-arrival-rate",
      rate: GRAPHQL_RPS,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 5,
      maxVUs: 20,
      exec: "graphql",
    },
  },
  thresholds: {
    // The redirect is the product. p99 at the 250 ms alert line; p95 from observation.
    // Calibrated on k3d + LocalStack (2 minutes at 20/s: p50 12.9 ms, p95 105 ms, p99
    // 180 ms), where the tail is DynamoDB Local's, not the service's -- its GetItem p99
    // reaches a full second at 40/s while the nodes sit 80% idle. Real DynamoDB answers
    // in single-digit milliseconds, so the burst run should tighten these, and the
    // tracker records what it measured.
    "linkpulse_redirect_duration": ["p(95)<150", "p(99)<250"],
    "linkpulse_graphql_duration": ["p(95)<500"],
    // 0.5%: an order of magnitude under the 5% alert.
    "http_req_failed": ["rate<0.005"],
    "linkpulse_redirect_not_302": ["count==0"],
    // Open model: if the service cannot keep up, k6 reports dropped iterations rather
    // than silently sending less. Any at all means the arrival rate was not sustained.
    "dropped_iterations": ["count==0"],
  },
  // 302 is the assertion, not something to follow: following it would send traffic to
  // example.com and time the wrong server.
  maxRedirects: 0,
  insecureSkipTLSVerify: true, // only matters with an https BASE_URL; the chain is tls-check.py's job
  // p(99) is not in k6's default export set, and it is the number the alert line is
  // drawn at.
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

const GQL_CREATE = `mutation($u:String!,$o:String!){ shortenUrl(longUrl:$u, ownerId:$o){ code } }`;
const GQL_LINK = `query($c:String!){ link(code:$c){ code totalClicks active } }`;
const GQL_OWNER = `query($o:String!){ links(ownerId:$o){ code } }`;

function gql(query, variables) {
  return http.post(`${BASE}/graphql`, JSON.stringify({ query, variables }), {
    headers: { "Content-Type": "application/json" },
    tags: { name: "graphql" },
  });
}

// Create a pool of links once. Every VU picks from the same pool, so the redirect
// scenario exercises A1 (resolve) and A3 (record a click) across many keys -- the
// normal, sharded, un-hot shape. throttle.js is the one that concentrates on one key.
export function setup() {
  const codes = [];
  for (let i = 0; i < LINKS; i++) {
    const res = gql(GQL_CREATE, { u: `https://example.com/baseline/${Date.now()}/${i}`, o: "k6-baseline" });
    const body = res.json();
    if (res.status !== 200 || !body.data || !body.data.shortenUrl) {
      throw new Error(`setup: could not create link ${i}: HTTP ${res.status} ${res.body}`);
    }
    codes.push(body.data.shortenUrl.code);
  }
  return { codes };
}

export function redirect(data) {
  const code = data.codes[Math.floor(Math.random() * data.codes.length)];
  const res = http.get(`${BASE}/r/${code}`, { redirects: 0, tags: { name: "redirect" } });
  redirectLatency.add(res.timings.duration);
  const ok = check(res, {
    "redirect is 302": (r) => r.status === 302,
    "Location is the long URL": (r) => (r.headers["Location"] || "").startsWith("https://example.com/baseline/"),
  });
  if (!ok) redirectsNot302.add(1);
}

export function graphql(data) {
  const code = data.codes[Math.floor(Math.random() * data.codes.length)];
  // Alternate between the two read patterns a dashboard actually issues.
  const res = Math.random() < 0.5 ? gql(GQL_LINK, { c: code }) : gql(GQL_OWNER, { o: "k6-baseline" });
  graphqlLatency.add(res.timings.duration);
  check(res, {
    "graphql is 200": (r) => r.status === 200,
    "graphql has no errors": (r) => !(r.json("errors") || []).length,
  });
}

// No handleSummary: k6's default text report is printed and --summary-export writes the
// full JSON. `mise run k6-baseline` then prints the one line the tracker quotes from that
// JSON (scripts/k6-summary.py), rather than importing jslib.k6.io at run time -- a remote
// import in the script would make the baseline need the internet to report on a cluster
// that does not.
