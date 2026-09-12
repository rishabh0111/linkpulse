// Experiment 1's traffic: every redirect on ONE link, well past the table's write budget.
//
// baseline.js spreads clicks over many links; this concentrates them, because the failure
// under test is the click aggregate for a single popular link. Each redirect costs 2 WCU
// of asynchronous writes (docs/data-model.md), so 40/s is 80 WCU/s against a table
// provisioned at 20 -- four times the ceiling, sustained, which on real DynamoDB throttles
// inside the first minute once the burst bucket drains. Locally LocalStack enforces no
// capacity at all, so scripts/chaos/experiment1.py injects the throttle while this runs
// (docker-compose.yml, ENABLE_CONFIG_UPDATES); the traffic shape is the same either way.
//
// The thresholds encode the experiment's central claim: redirects stay fast and stay 302
// WHILE the writes behind them are being refused. If the p99 here blows out during the
// throttle, the asynchronous click design has failed and the experiment should say so.
//
//   docker compose run --rm k6 run load-testing/k6/throttle.js            # 3 minutes at 40/s
//   docker compose run --rm -e RATE=60 -e DURATION=10m k6 run load-testing/k6/throttle.js
//
// Started detached by `mise run chaos-1` (docker compose run -d) so the assertions can
// run beside it; on the burst it is run in the foreground against the ALB.

import http from "k6/http";
import { check } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE = (__ENV.BASE_URL || "http://k3d-linkpulse-serverlb").replace(/\/$/, "");
const RATE = Number(__ENV.RATE || 40);
const DURATION = __ENV.DURATION || "3m";

const redirectLatency = new Trend("linkpulse_redirect_duration", true);
const redirectsNot302 = new Counter("linkpulse_redirect_not_302");

export const options = {
  scenarios: {
    hot_link: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 30,
      maxVUs: 150,
    },
  },
  thresholds: {
    // 250 ms is the alert line and the burst's number. Locally `mise run chaos-1` passes
    // P99_MS=1000, because DynamoDB Local answers a single hot key in ~250 ms p99 with no
    // fault injected at all (measured in the phase-9 exploratory pass) -- the relative
    // check in experiment1.py is the one that means something there.
    "linkpulse_redirect_duration": [`p(99)<${Number(__ENV.P99_MS || 250)}`],
    "linkpulse_redirect_not_302": ["count==0"],
    "http_req_failed": ["rate<0.005"],
    "dropped_iterations": ["count==0"],
  },
  maxRedirects: 0,
  insecureSkipTLSVerify: true,
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

export function setup() {
  const res = http.post(
    `${BASE}/graphql`,
    JSON.stringify({
      query: `mutation($u:String!,$o:String!){ shortenUrl(longUrl:$u, ownerId:$o){ code } }`,
      variables: { u: `https://example.com/hot/${Date.now()}`, o: "k6-throttle" },
    }),
    { headers: { "Content-Type": "application/json" } },
  );
  const body = res.json();
  if (res.status !== 200 || !body.data || !body.data.shortenUrl) {
    throw new Error(`setup: could not create the hot link: HTTP ${res.status} ${res.body}`);
  }
  console.log(`hot link: ${body.data.shortenUrl.code}`);
  return { code: body.data.shortenUrl.code };
}

export default function (data) {
  const res = http.get(`${BASE}/r/${data.code}`, { redirects: 0, tags: { name: "redirect" } });
  redirectLatency.add(res.timings.duration);
  if (!check(res, { "redirect is 302": (r) => r.status === 302 })) redirectsNot302.add(1);
}
