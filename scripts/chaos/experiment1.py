#!/usr/bin/env python3
"""Chaos experiment 1: DynamoDB throttles the click writes.

The failure the data model was designed around (docs/data-model.md section 3): one popular
link, every click an UpdateItem on the same aggregate, the table refusing writes. The
claim under test is the design's answer to it -- clicks are written asynchronously, so
the user-visible symptom is LOST ANALYTICS, not slow redirects -- and the alert that
exists because of that claim, LinkpulseClicksDropped{reason="throttled"}, which watches
the drop counter rather than latency.

Load is load-testing/k6/throttle.js, started detached by `mise run chaos-1` before the
`during` phase and left running through it: 20 redirects/s on ONE link, 40 WCU/s of
writes against a 20 WCU table. The throttle itself:

  locally   LocalStack enforces no capacity at all (measured: 78 WCU/s, zero throttles),
            so this script injects it -- DYNAMODB_WRITE_ERROR_PROBABILITY=0.5 through
            LocalStack's runtime config endpoint, which makes half of all PutItem and
            UpdateItem calls fail with a genuine ProvisionedThroughputExceededException.
            The exception, the SDK's retry, the drop, the counter, the alert and the
            dashboard are all real; only the reason DynamoDB gave it is not.
  burst     nothing is injected. The same k6 run against the real table crosses 20 WCU
            and DynamoDB throttles on its own once the burst bucket drains; the `during`
            phase is run with CHAOS_INJECT=none and asserts the identical signature.

Two windows are compared: one minute of clean load before the injection, and the throttle
itself. Redirect p99 during must stay within the alert line (250 ms) OR within 2x the
clean window -- the second clause is for LocalStack, whose DynamoDB Local answers a hot
key in 200 ms p99 with no fault at all, and the first is what the burst run should meet.
The 5xx ratio must be exactly zero throughout: the whole point is that throttling never
reaches a user as an error.

    mise run chaos-1
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import chaos as c  # noqa: E402

RUN = c.Run(1, "DynamoDB write throttling on a hot link")
LOCALSTACK = os.environ.get("CHAOS_LOCALSTACK", "http://localstack:4566")
INJECT = os.environ.get("CHAOS_INJECT", "localstack")  # "localstack" or "none" (the burst)
PROBABILITY = float(os.environ.get("CHAOS_WRITE_ERROR_PROBABILITY", "0.5"))
K6_SUMMARY = os.environ.get("CHAOS_K6_SUMMARY", "load-testing/k6/output/chaos-1-throttle.json")

DROPPED = 'sum(rate(linkpulse_clicks_dropped_total{reason="throttled"}[1m]))'
THROTTLED = "sum(rate(linkpulse_dynamodb_throttles_total[1m]))"
RECORDED = "sum(rate(linkpulse_clicks_recorded_total[1m]))"
REDIRECT_P99 = 'histogram_quantile(0.99, sum by (le) (rate(linkpulse_http_request_duration_seconds_bucket{route="redirect"}[1m])))'
REDIRECT_RATE = 'sum(rate(linkpulse_http_requests_total{route="redirect"}[1m]))'
ERROR_RATIO = ('sum(rate(linkpulse_http_requests_total{code=~"5..", route!~"healthz|readyz"}[1m])) / '
               'sum(rate(linkpulse_http_requests_total{route!~"healthz|readyz"}[1m]))')


def set_write_error_probability(p: float) -> None:
    body = json.dumps({"variable": "DYNAMODB_WRITE_ERROR_PROBABILITY", "value": p}).encode()
    req = urllib.request.Request(LOCALSTACK + "/_localstack/config", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        out = json.load(resp)
    if float(out.get("value", -1)) != p:
        raise c.ChaosError(f"LocalStack did not take the config update: {out} (is ENABLE_CONFIG_UPDATES=1?)")


def pre() -> None:
    RUN.start().phase("pre: the healthy state")
    c.require_healthy(RUN)
    shards = c.prom_scalar("max(linkpulse_click_shards)")
    RUN.fact("clickShards", shards)
    c.check(RUN, "no clicks being dropped for throttling", (c.prom_scalar(DROPPED, 0) or 0) == 0)
    c.check(RUN, "LinkpulseClicksDropped not firing", c.alert_state("LinkpulseClicksDropped") is None)
    if INJECT == "localstack":
        set_write_error_probability(0.0)
        RUN.mark("LocalStack write-error probability confirmed 0 (injection armed, not applied)")
    RUN.mark("starting load: k6 throttle.js, 20 redirects/s on one link")


def during() -> None:
    RUN.load().phase("during: load, then the throttle")

    # A clean window first: prove the load is landing and nothing is dropped WITHOUT the
    # fault, so that what follows is attributable to the fault and not to the load.
    c.wait_for(RUN, "redirects arriving at >= 15/s", lambda: (c.prom_scalar(REDIRECT_RATE, 0) or 0) >= 15, timeout=120)
    time.sleep(45)  # let the 1m windows fill with clean traffic
    clean_p99 = c.prom_scalar(REDIRECT_P99, 0) or 0
    clean_recorded = c.prom_scalar(RECORDED, 0) or 0
    RUN.fact("cleanWindow", {"redirectP99Ms": round(clean_p99 * 1000, 1), "clicksRecordedPerS": round(clean_recorded, 1)})
    c.check(RUN, "clean window: clicks recorded at >= 15/s", clean_recorded >= 15, f"{clean_recorded:.1f}/s")
    c.check(RUN, "clean window: nothing dropped", (c.prom_scalar(DROPPED, 0) or 0) == 0)

    if INJECT == "localstack":
        set_write_error_probability(PROBABILITY)
        RUN.mark(f"injecting: DYNAMODB_WRITE_ERROR_PROBABILITY={PROBABILITY} via LocalStack config")
    else:
        RUN.mark("no injection: waiting for the real table to throttle on its own")
    t0 = c.now()

    t_drop, _ = c.wait_for(RUN, "clicks dropped with reason=throttled (rate > 0)",
                           lambda: c.prom_scalar(DROPPED, 0) or 0, timeout=300)
    t_alert, _ = c.wait_for(RUN, "LinkpulseClicksDropped{reason=throttled} firing",
                            lambda: c.alert_state("LinkpulseClicksDropped", reason="throttled") == "firing", timeout=180)
    c.wait_for(RUN, "LinkpulseDynamoThrottled firing", lambda: c.alert_state("LinkpulseDynamoThrottled") is not None, timeout=120)
    RUN.fact("secondsToFirstDrop", round(t_drop))
    RUN.fact("secondsToAlert", round(t_drop + t_alert))

    # The signature: analytics lost, redirects unaffected, users see no errors.
    throttled = c.prom_scalar(THROTTLED, 0) or 0
    drop_rate = c.prom_scalar(DROPPED, 0) or 0
    recorded = c.prom_scalar(RECORDED, 0) or 0
    p99 = c.prom_scalar(REDIRECT_P99, 0) or 0
    err = c.prom_scalar(ERROR_RATIO, 0) or 0
    rate = c.prom_scalar(REDIRECT_RATE, 0) or 0
    RUN.fact("throttleWindow", {"redirectP99Ms": round(p99 * 1000, 1), "redirectsPerS": round(rate, 1),
                                "clicksDroppedPerS": round(drop_rate, 1), "clicksRecordedPerS": round(recorded, 1),
                                "dynamoThrottlesPerS": round(throttled, 1), "errorRatio": err})
    c.check(RUN, "DynamoDB reporting throttled calls", throttled > 0, f"{throttled:.1f}/s")
    c.check(RUN, "redirects still arriving at >= 15/s", rate >= 15, f"{rate:.1f}/s")
    c.check(RUN, "no user-facing 5xx at all", err == 0, f"ratio {err}")
    limit = max(0.25, 2 * clean_p99)
    c.check(RUN, f"redirect p99 within {limit * 1000:.0f}ms (alert line, or 2x the clean window)",
            p99 <= limit, f"{p99 * 1000:.0f}ms vs clean {clean_p99 * 1000:.0f}ms")
    RUN.fact("throttleHeldSeconds", round(c.now() - t0))

    if INJECT == "localstack":
        set_write_error_probability(0.0)
        RUN.mark("recovering: DYNAMODB_WRITE_ERROR_PROBABILITY=0")
    else:
        RUN.mark("recovering: load ends; the table's bucket refills")

    t_res, _ = c.wait_for(RUN, "drops back to 0/s", lambda: (c.prom_scalar(DROPPED, 0) or 0) == 0, timeout=180)
    c.wait_for(RUN, "LinkpulseClicksDropped resolved", lambda: c.alert_state("LinkpulseClicksDropped") is None, timeout=300)
    RUN.fact("secondsToResolveAfterRecovery", round(t_res))


def post() -> None:
    RUN.load().phase("post: the load's own verdict")
    if os.path.exists(K6_SUMMARY):
        m = json.load(open(K6_SUMMARY, encoding="utf-8"))["metrics"]
        t = m.get("linkpulse_redirect_duration", {})
        RUN.fact("k6", {"requests": m.get("http_reqs", {}).get("count"), "p50Ms": t.get("med"), "p95Ms": t.get("p(95)"),
                        "p99Ms": t.get("p(99)"), "failedRatio": m.get("http_req_failed", {}).get("value"),
                        "not302": m.get("linkpulse_redirect_not_302", {}).get("count", 0)})
        c.check(RUN, "k6: every redirect was a 302", (m.get("linkpulse_redirect_not_302", {}).get("count", 0) or 0) == 0)
        c.check(RUN, "k6: no failed requests", (m.get("http_req_failed", {}).get("value", 0) or 0) == 0)
    else:
        RUN.mark("k6 summary not found; latency verdict from Prometheus only", K6_SUMMARY)
    RUN.finish(panels=[
        ("linkpulse-overview", 13, "clicks-recorded-vs-dropped"),
        ("linkpulse-overview", 16, "dynamodb-throttles"),
        ("linkpulse-overview", 10, "redirect-latency"),
        ("linkpulse-overview", 9, "5xx-ratio"),
    ])


if __name__ == "__main__":
    sys.exit(c.cli({"pre": pre, "during": during, "post": post}))
