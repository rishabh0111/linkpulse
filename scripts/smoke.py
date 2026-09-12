#!/usr/bin/env python3
"""End-to-end smoke test for a running LinkPulse API.

Exercises every access pattern in docs/data-model.md against a live service and a live
table, and exits non-zero if any of them is wrong. This is the gate that catches the class
of bug unit tests structurally cannot: a key built one way in code and another way in
Terraform, an IAM policy missing the index ARN, a timestamp that serialises as Go's native
format instead of RFC3339.

Standard library only, on purpose. The chaos and CI paths run this inside a plain python
image with nothing installed, so it cannot depend on requests, curl or jq being present.

    python3 scripts/smoke.py --base-url http://localhost:8080
    python3 scripts/smoke.py --base-url http://linkpulse-api:8080 --clicks 60
    python3 scripts/smoke.py --base-url https://k3d-linkpulse-serverlb --ca-cert certs/ca.crt

With --ca-cert the whole run goes through TLS and the chain is *verified* against the
cluster's own CA (exported by `mise run tls-ca`). Not --insecure: a check that skips
verification would pass against Traefik's default self-signed certificate and prove
nothing about the one cert-manager issued.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 15

# Set from --ca-cert. None means the default trust store, which is right for plain http
# and for a real certificate; only the local CA needs to be passed in explicitly.
SSL_CONTEXT: ssl.SSLContext | None = None


class SmokeError(Exception):
    """A failed assertion. Carries the message shown to the operator."""


def request(method: str, url: str, body: dict | None = None, redirect: bool = True):
    """One HTTP call. Returns (status, headers, decoded-body-or-text)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    handlers: list[urllib.request.BaseHandler] = []
    if SSL_CONTEXT is not None:
        handlers.append(urllib.request.HTTPSHandler(context=SSL_CONTEXT))
    if not redirect:
        # The redirect hot path is the thing under test: following the 302 would send a
        # request to the real destination and assert nothing about LinkPulse.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None

        handlers.append(NoRedirect())
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            return resp.status, dict(resp.headers), decode(raw)
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        return err.code, dict(err.headers), decode(raw)


def decode(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def gql(base: str, query: str, variables: dict | None = None) -> dict:
    """Run a GraphQL operation and fail loudly on any error in the response."""
    status, _, body = request("POST", f"{base}/graphql", {"query": query, "variables": variables or {}})
    if status != 200:
        raise SmokeError(f"graphql returned HTTP {status}: {body}")
    if not isinstance(body, dict):
        raise SmokeError(f"graphql returned non-JSON: {body!r}")
    if body.get("errors"):
        raise SmokeError(f"graphql errors: {body['errors']}")
    return body["data"]


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
        return
    raise SmokeError(f"{label}{': ' + detail if detail else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--clicks", type=int, default=25, help="redirects to drive")
    parser.add_argument(
        "--expect-shards",
        type=int,
        default=None,
        help="assert the service reports this CLICK_SHARDS value (experiment 1 uses it)",
    )
    parser.add_argument(
        "--ca-cert",
        default=None,
        help="PEM CA to verify TLS against (from `mise run tls-ca`); implies an https base URL",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    global SSL_CONTEXT
    if args.ca_cert:
        if not base.startswith("https://"):
            parser.error("--ca-cert only makes sense with an https:// base URL")
        # create_default_context verifies the chain AND the hostname against the SAN
        # list; both stay on. cafile replaces the system store rather than adding to it,
        # so a certificate that is not from the local CA fails here even if some other
        # authority would have accepted it.
        SSL_CONTEXT = ssl.create_default_context(cafile=args.ca_cert)

    print(f"smoke: {base}" + (f"  (tls verified against {args.ca_cert})" if args.ca_cert else ""))

    # ---- probes -------------------------------------------------------------------
    print("probes")
    status, _, body = request("GET", f"{base}/healthz")
    check("liveness is 200", status == 200, f"got {status}")

    status, _, ready = request("GET", f"{base}/readyz")
    check("readiness is 200", status == 200, f"got {status}: {ready}")
    if args.expect_shards is not None:
        got = ready.get("clickShards") if isinstance(ready, dict) else None
        check(f"CLICK_SHARDS is {args.expect_shards}", got == args.expect_shards, f"got {got}")

    # ---- A2: create ---------------------------------------------------------------
    print("A2 create")
    long_url = "https://example.com/smoke/" + str(int(time.time()))
    owner = "smoke-runner"
    data = gql(
        base,
        "mutation($u:String!,$o:String!){ shortenUrl(longUrl:$u, ownerId:$o){"
        " code shortUrl longUrl ownerId active createdAt } }",
        {"u": long_url, "o": owner},
    )
    link = data["shortenUrl"]
    code = link["code"]
    check("code returned", bool(code))
    check("longUrl round-trips", link["longUrl"] == long_url, link["longUrl"])
    check("new link is active", link["active"] is True)
    check("createdAt is RFC3339", is_rfc3339(link["createdAt"]), link["createdAt"])

    # A dangerous URL must be refused, and as a 400 rather than a 500 — the alerting
    # divides by status, so a rejected input must not look like an outage.
    status, _, body = request(
        "POST",
        f"{base}/graphql",
        {"query": 'mutation{ shortenUrl(longUrl:"javascript:alert(1)"){ code } }'},
    )
    check("javascript: URL rejected as 4xx", status == 400, f"got {status}")

    # ---- A1: redirect -------------------------------------------------------------
    print("A1 redirect")
    status, headers, _ = request("GET", f"{base}/r/{code}", redirect=False)
    check("redirect is 302", status == 302, f"got {status}")
    check("Location is the long URL", headers.get("Location") == long_url, headers.get("Location"))

    status, _, _ = request("GET", f"{base}/r/zzzzzzz", redirect=False)
    check("unknown code is 404", status == 404, f"got {status}")

    status, _, _ = request("GET", f"{base}/r/not-a-valid-code", redirect=False)
    check("malformed code is 404", status == 404, f"got {status}")

    # ---- A3: clicks ---------------------------------------------------------------
    print(f"A3 clicks ({args.clicks} redirects)")
    for _ in range(args.clicks):
        request("GET", f"{base}/r/{code}", redirect=False)
    expected = args.clicks + 1  # the assertion above was itself a click

    # Clicks are written asynchronously, so the count is eventually consistent by
    # design. Poll rather than sleep-and-hope: a fixed sleep is how this kind of check
    # becomes flaky in CI.
    total = wait_for_total(base, code, expected, deadline_s=30)
    check(f"all {expected} clicks recorded", total == expected, f"got {total}")

    # ---- A4/A5/A6 -----------------------------------------------------------------
    print("A4/A5/A6 analytics")
    data = gql(
        base,
        "query($c:String!){ link(code:$c){ totalClicks clicksByDay{ day count }"
        " recentClicks(limit:5){ at uaClass } } }",
        {"c": code},
    )
    stats = data["link"]
    check("totalClicks matches", stats["totalClicks"] == expected, str(stats["totalClicks"]))

    days = stats["clicksByDay"]
    check("clicksByDay has today", len(days) >= 1, str(days))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    check("today's bucket is present", any(d["day"] == today for d in days), str(days))
    check(
        "per-day counts sum to the total",
        sum(d["count"] for d in days) == expected,
        str(days),
    )

    feed = stats["recentClicks"]
    check("click feed is populated", len(feed) > 0)
    check("feed timestamps are RFC3339", all(is_rfc3339(c["at"]) for c in feed), str(feed[:1]))
    stamps = [c["at"] for c in feed]
    check("feed is newest-first", stamps == sorted(stamps, reverse=True), str(stamps[:3]))

    # ---- A7: sparse GSI1 ----------------------------------------------------------
    # This is the pattern that fails with AccessDenied when an IAM policy covers the
    # table ARN but not the index ARN, so it is worth its own assertion.
    print("A7 owner index")
    data = gql(base, "query($o:String!){ links(ownerId:$o){ code longUrl active } }", {"o": owner})
    codes = [item["code"] for item in data["links"]]
    check("owner's link found via GSI1", code in codes, str(codes))

    # ---- metrics ------------------------------------------------------------------
    # The chaos assertions query these by name. If a rename ever breaks an alert, it
    # should break here first.
    print("metrics")
    status, _, text = request("GET", f"{base}/metrics")
    check("metrics endpoint is 200", status == 200, f"got {status}")
    for series in (
        "linkpulse_http_requests_total",
        "linkpulse_http_request_duration_seconds",
        "linkpulse_redirects_total",
        "linkpulse_clicks_recorded_total",
        "linkpulse_clicks_dropped_total",
        "linkpulse_click_shards",
        "linkpulse_dynamodb_requests_total",
        "linkpulse_ready",
    ):
        check(f"{series} exposed", series in text)

    print("\nsmoke: PASS")
    return 0


def wait_for_total(base: str, code: str, expected: int, deadline_s: int) -> int:
    """Poll totalClicks until it reaches expected or the deadline passes."""
    deadline = time.time() + deadline_s
    total = -1
    while time.time() < deadline:
        data = gql(base, "query($c:String!){ link(code:$c){ totalClicks } }", {"c": code})
        total = data["link"]["totalClicks"]
        if total >= expected:
            return total
        time.sleep(0.5)
    return total


def is_rfc3339(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        # fromisoformat handles the offset and fractional seconds; the Z has to be
        # translated because Python did not accept it before 3.11.
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeError as exc:
        print(f"\n  FAIL  {exc}", file=sys.stderr)
        print("smoke: FAIL", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"\nsmoke: could not reach the service: {exc}", file=sys.stderr)
        sys.exit(2)
