#!/usr/bin/env python3
"""Chaos experiment 2: a bad deploy, through the real pipeline, and the rollback.

The image is linkpulse:bad -- the same source built with BREAK_READINESS=1, so /readyz
fails while /healthz passes. That is the signature of the most common real bad deploy: a
container that starts, stays up, and cannot serve. It is not pushed with kubectl; it is
committed (experiment2-git.sh flips the local overlay's image tag) and ArgoCD rolls it
out, because what is under test is the delivery path, not the Deployment object alone.

What should happen, and what the phases assert:

  during   ArgoCD syncs the commit; a new pod appears with the bad image and never
           becomes Ready; the OLD pods keep serving because maxUnavailable is 0 and the
           surge pod is the one stuck; after progressDeadlineSeconds (120s) the
           Deployment reports Progressing=False / ProgressDeadlineExceeded;
           LinkpulseRolloutStalled fires on that condition; ArgoCD reports the
           Application Degraded; no user-facing error at any point.
  post     the fix is `git revert`, pushed; ArgoCD syncs it; the stuck pod is removed,
           the rollout completes on the original image, the alert resolves, and the
           Application is Healthy again. Nobody touched the cluster.

    mise run chaos-2
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import chaos as c  # noqa: E402

RUN = c.Run(2, "bad deploy through GitOps, and the rollback")
STALLED = ('kube_deployment_status_condition{namespace="linkpulse", deployment="linkpulse", '
           'condition="Progressing", status="false"}')
ERROR_RATIO = ('sum(rate(linkpulse_http_requests_total{code=~"5..", route!~"healthz|readyz"}[2m])) / '
               'sum(rate(linkpulse_http_requests_total{route!~"healthz|readyz"}[2m]))')


def pod_images() -> dict[str, tuple[str, bool]]:
    """pod -> (image, ready)."""
    out = {}
    for p in c.app_pods():
        if p["metadata"].get("deletionTimestamp"):
            continue
        img = p["spec"]["containers"][0]["image"]
        conds = {cd["type"]: cd["status"] for cd in p.get("status", {}).get("conditions", [])}
        out[p["metadata"]["name"]] = (img, conds.get("Ready") == "True")
    return out


def deployment_conditions() -> dict[str, dict]:
    d = c.kubectl("get", "deploy", "linkpulse", "-n", "linkpulse")
    return {cd["type"]: cd for cd in d.get("status", {}).get("conditions", [])}


def redirect_serves() -> bool:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(c.INGRESS + "/r/zzzzzzz", headers={"Host": f"linkpulse.{c.DOMAIN}"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        return exc.code == 404
    except OSError:
        return False
    return False


def pushed_revision(key: str) -> str:
    """The short hash experiment2-git.sh printed, handed over by the mise task through
    a file so that no shell has to capture and re-quote it."""
    path = os.path.join(RUN.dir, f"{key}.rev")
    if not os.path.exists(path):
        raise c.ChaosError(f"{path} missing: did experiment2-git.sh run?")
    return open(path, encoding="utf-8").read().strip()[:7]


def pre() -> None:
    RUN.start().phase("pre: the healthy state")
    c.require_healthy(RUN)
    app = c.argocd_app("linkpulse")
    c.check(RUN, "ArgoCD: linkpulse Synced and Healthy", app["sync"] == "Synced" and app["health"] == "Healthy", str(app))
    images = pod_images()
    good = {img for img, _ in images.values()}
    c.check(RUN, "every pod runs the same, good image", len(good) == 1 and all(r for _, r in images.values()), str(images))
    RUN.fact("goodImage", good.pop())
    RUN.fact("revisionAtStart", app["revision"])
    RUN.fact("podsAtStart", sorted(images))
    RUN.mark("injecting: experiment2-git.sh break (newTag: bad, pushed to Gitea)")


def during() -> None:
    RUN.load().phase("during: the stalled rollout")
    rev = pushed_revision("break")
    RUN.fact("breakRevision", rev)
    t0 = c.now()
    t_sync, _ = c.wait_for(RUN, f"ArgoCD synced the bad commit {rev}", lambda: c.argocd_app("linkpulse")["revision"] == rev, timeout=240)
    RUN.fact("secondsToSync", round(t_sync))

    t_pod, _ = c.wait_for(RUN, "a pod with the bad image exists",
                          lambda: any(img.endswith(":bad") for img, _ in pod_images().values()), timeout=120, interval=3)
    old_ready = [p for p, (img, r) in pod_images().items() if not img.endswith(":bad") and r]
    c.check(RUN, "the old pods are still Ready (maxUnavailable: 0)", len(old_ready) >= 2, str(pod_images()))
    c.check(RUN, "redirect path served by the old pods", redirect_serves())

    t_stall, _ = c.wait_for(RUN, "Deployment Progressing=False (ProgressDeadlineExceeded)",
                            lambda: deployment_conditions().get("Progressing", {}).get("status") == "False", timeout=300)
    cond = deployment_conditions().get("Progressing", {})
    RUN.fact("progressingCondition", {"reason": cond.get("reason"), "message": cond.get("message")})
    c.check(RUN, "reason is ProgressDeadlineExceeded", cond.get("reason") == "ProgressDeadlineExceeded", str(cond.get("reason")))
    RUN.fact("secondsToProgressDeadline", round(c.now() - t0))

    t_alert, _ = c.wait_for(RUN, "LinkpulseRolloutStalled firing", lambda: c.alert_state("LinkpulseRolloutStalled") is not None, timeout=240)
    RUN.fact("secondsToAlert", round(c.now() - t0))
    c.wait_for(RUN, "ArgoCD reports the Application Degraded", lambda: c.argocd_app("linkpulse")["health"] == "Degraded", timeout=180)

    bad_pods = [p for p, (img, r) in pod_images().items() if img.endswith(":bad")]
    c.check(RUN, "the bad pod never became Ready", all(not pod_images()[p][1] for p in bad_pods), str(pod_images()))
    c.check(RUN, "still >= 2 old pods Ready", len([1 for img, r in pod_images().values() if not img.endswith(":bad") and r]) >= 2)
    c.check(RUN, "no user-facing 5xx", (c.prom_scalar(ERROR_RATIO, 0) or 0) == 0)
    c.check(RUN, "redirect path still served", redirect_serves())
    RUN.fact("podsDuringStall", pod_images())
    RUN.mark("recovering: experiment2-git.sh revert (git revert, pushed)")


def post() -> None:
    RUN.load().phase("post: the rollback")
    rev = pushed_revision("revert")
    RUN.fact("revertRevision", rev)
    t0 = c.now()
    t_sync, _ = c.wait_for(RUN, f"ArgoCD synced the revert {rev}", lambda: c.argocd_app("linkpulse")["revision"] == rev, timeout=240)
    c.wait_for(RUN, "no pod with the bad image remains", lambda: not any(img.endswith(":bad") for img, _ in pod_images().values()),
               timeout=180, interval=3)
    c.wait_for(RUN, "Deployment Progressing=True again (NewReplicaSetAvailable)",
               lambda: deployment_conditions().get("Progressing", {}).get("reason") == "NewReplicaSetAvailable", timeout=180)
    c.wait_for(RUN, "LinkpulseRolloutStalled resolved", lambda: c.alert_state("LinkpulseRolloutStalled") is None, timeout=300)
    c.wait_for(RUN, "ArgoCD: Synced and Healthy",
               lambda: c.argocd_app("linkpulse")["sync"] == "Synced" and c.argocd_app("linkpulse")["health"] == "Healthy", timeout=300)
    RUN.fact("secondsToRollBack", round(c.now() - t0))
    images = pod_images()
    c.check(RUN, "every pod runs the good image and is Ready",
            all(img == RUN.data["facts"]["goodImage"] and r for img, r in images.values()) and len(images) >= 2, str(images))
    c.check(RUN, "redirect path served", redirect_serves())
    RUN.finish(panels=[
        ("linkpulse-kubernetes", 8, "replicas"),
        ("linkpulse-kubernetes", 4, "rollout-stalled"),
        ("linkpulse-kubernetes", 10, "pod-readiness"),
        ("linkpulse-overview", 9, "5xx-ratio"),
    ])


if __name__ == "__main__":
    sys.exit(c.cli({"pre": pre, "during": during, "post": post}))
