#!/usr/bin/env python3
"""Chaos experiment 3: the datastore goes away.

Locally the injection is `docker compose pause localstack` -- the container is frozen, so
every DynamoDB call from the pods hangs and then times out, while the table itself
survives for the recovery. On EKS the equivalent is detaching the IAM policy from the
IRSA role (envs/aws outputs `app_policy_arn` for exactly this), which fails the same
calls with AccessDenied instead of a timeout; the application's readiness check does not
distinguish, and the alerts should not either.

What the experiment asserts, in order:

  during   the process notices first (linkpulse_ready -> 0, LinkpulseNotReady fires),
           Kubernetes agrees after failureThreshold x periodSeconds (kube_pod_status_ready
           -> false, LinkpulseNoReadyPods fires), the inhibit rule then SUPPRESSES the
           per-pod alert so a human gets one page not three, the Service has no endpoints
           so the ingress answers 5xx -- and no container restarts, because liveness
           deliberately does not check the store.
  post     everything above reverses without a human touching a pod: ready pods back,
           both alerts resolved, restart count unchanged.

The numbers the phase-7 outage test measured by hand -- NotReady at ~100s, NoReadyPods at
~140s -- are the starting point for the timeouts below; the timeline this writes is the
measurement of record.

    mise run chaos-3
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import chaos as c  # noqa: E402

RUN = c.Run(3, "dependency outage (datastore unreachable)")


def restarts() -> float:
    return c.prom_scalar('sum(kube_pod_container_status_restarts_total{namespace="linkpulse", container="api"})', 0.0) or 0.0


def redirect_status() -> int:
    """Status of a redirect for a code that cannot exist. 404 means the service answered
    (it looked and found nothing); 5xx means nothing answered behind the ingress."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(c.INGRESS + "/r/zzzzzzz", headers={"Host": f"linkpulse.{c.DOMAIN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def pre() -> None:
    RUN.start().phase("pre: the healthy state")
    c.require_healthy(RUN)
    RUN.fact("restartsAtStart", restarts())
    c.check(RUN, "redirect path answers (404 for an unknown code)", redirect_status() == 404)
    c.check(RUN, "process reports ready on every pod", c.prom_scalar("min(linkpulse_ready)", 0) == 1)
    RUN.mark("injecting: docker compose pause localstack")


def during() -> None:
    RUN.load().phase("during: the outage")
    t_not_ready, _ = c.wait_for(RUN, "linkpulse_ready == 0 on every pod (process view)",
                                lambda: c.prom_scalar("max(linkpulse_ready)", 1) == 0, timeout=120)
    t_alert1, _ = c.wait_for(RUN, "LinkpulseNotReady firing",
                             lambda: c.alert_state("LinkpulseNotReady") is not None, timeout=240)
    t_k8s, _ = c.wait_for(RUN, "0 ready pods (Kubernetes view)", lambda: len(c.ready_app_pods()) == 0, timeout=180)
    t_alert2, _ = c.wait_for(RUN, "LinkpulseNoReadyPods firing",
                             lambda: c.alert_state("LinkpulseNoReadyPods") == "firing", timeout=300)
    RUN.fact("secondsToProcessNotReady", round(t_not_ready))
    RUN.fact("secondsToNotReadyAlert", round(t_not_ready + t_alert1))
    RUN.fact("secondsToKubernetesNotReady", round(t_not_ready + t_alert1 + t_k8s))
    RUN.fact("secondsToNoReadyPodsAlert", round(t_not_ready + t_alert1 + t_k8s + t_alert2))

    # The inhibit rule is the difference between one page and three. Give Alertmanager a
    # moment to re-evaluate, then require it.
    c.wait_for(RUN, "LinkpulseNotReady inhibited by LinkpulseNoReadyPods",
               lambda: c.alert_state("LinkpulseNotReady") == "inhibited", timeout=90)

    status = redirect_status()
    c.check(RUN, "ingress answers 5xx with no endpoints behind the Service", 500 <= status < 600, f"HTTP {status}")
    c.check(RUN, "no container restarted (liveness ignores the dependency)",
            restarts() == RUN.data["facts"]["restartsAtStart"], f"restarts={restarts()}")
    c.check(RUN, "the pods are still Running, merely not Ready",
            all(p["status"]["phase"] == "Running" for p in c.app_pods()))
    RUN.mark("recovering: docker compose unpause localstack")


def post() -> None:
    RUN.load().phase("post: recovery")
    t_ready, _ = c.wait_for(RUN, "2 ready pods again", lambda: len(c.ready_app_pods()) >= 2, timeout=180)
    RUN.fact("secondsToRecoverReady", round(t_ready))
    c.wait_for(RUN, "both alerts resolved",
               lambda: c.alert_state("LinkpulseNotReady") is None and c.alert_state("LinkpulseNoReadyPods") is None,
               timeout=300)
    c.check(RUN, "still no container restarted", restarts() == RUN.data["facts"]["restartsAtStart"], f"restarts={restarts()}")
    c.check(RUN, "redirect path answers again", redirect_status() == 404)
    RUN.finish(panels=[
        ("linkpulse-overview", 18, "readiness-process-view"),
        ("linkpulse-kubernetes", 10, "readiness-kubernetes-view"),
        ("linkpulse-kubernetes", 15, "container-restarts"),
    ])


if __name__ == "__main__":
    sys.exit(c.cli({"pre": pre, "during": during, "post": post}))
