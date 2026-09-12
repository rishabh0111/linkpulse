#!/usr/bin/env python3
"""Chaos experiment 4: one pod runs out of memory, repeatedly.

The fault is injected and the write-up says so: POST /debug/leak (local overlay only,
LINKPULSE_ENABLE_DEBUG_LEAK) allocates and retains memory on request, because load alone
does not push a Go service past its limit -- the GC keeps steady state flat -- and an
experiment that only sometimes triggers cannot carry an assertion. What follows the
injection is entirely genuine: the cgroup memory limit (128Mi), the OOM killer, the
restart, the kubelet's exponential backoff, CrashLoopBackOff, and the three alerts that
watch each stage.

One pod is targeted through a port-forward (the ingress load-balances, and the point is
that the OTHER replica keeps serving: a redirect through the ingress is checked at every
stage). The sequence per kill: leak to ~90% of the limit and hold there long enough for
LinkpulseMemoryNearLimit to fire, then push over. The first kill is enough for
LinkpulseOOMKilled; CrashLoopBackOff needs the kubelet's backoff to reach the alert's
`for: 1m`, which takes four kills (10s, 20s, 40s, 80s), so the leak is repeated on each
restarted container until the waiting reason has held for a minute.

    mise run chaos-4
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import chaos as c  # noqa: E402

RUN = c.Run(4, "resource exhaustion (OOMKilled to CrashLoopBackOff)")
LIMIT_MB = 128
LOCAL_PORT = 18080

def mem_ratio(pod: str) -> float:
    """Working set over limit for one pod, with the same join the alert rule uses."""
    return c.prom_scalar(
        f'max(container_memory_working_set_bytes{{namespace="linkpulse", container="api", pod="{pod}"}} '
        f'/ on (namespace, pod, container) group_left '
        f'kube_pod_container_resource_limits{{namespace="linkpulse", container="api", resource="memory", pod="{pod}"}})', 0) or 0


def restarts_of(pod: str) -> float:
    return c.prom_scalar(f'kube_pod_container_status_restarts_total{{namespace="linkpulse", pod="{pod}", container="api"}}', 0.0) or 0.0


def waiting_reason(pod: str) -> str | None:
    for p in c.app_pods():
        if p["metadata"]["name"] != pod:
            continue
        for cs in p.get("status", {}).get("containerStatuses", []):
            w = cs.get("state", {}).get("waiting")
            if w:
                return w.get("reason")
    return None


def last_termination(pod: str) -> str | None:
    for p in c.app_pods():
        if p["metadata"]["name"] == pod:
            for cs in p.get("status", {}).get("containerStatuses", []):
                t = cs.get("lastState", {}).get("terminated")
                if t:
                    return t.get("reason")
    return None


def container_running(pod: str) -> bool:
    for p in c.app_pods():
        if p["metadata"]["name"] == pod:
            return any("running" in cs.get("state", {}) and cs.get("ready") for cs in p.get("status", {}).get("containerStatuses", []))
    return False


def redirect_serves() -> bool:
    req = urllib.request.Request(c.INGRESS + "/r/zzzzzzz", headers={"Host": f"linkpulse.{c.DOMAIN}"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as exc:
        return exc.code == 404
    except OSError:
        return False
    return False


class Forward:
    """kubectl port-forward to one pod, torn down on exit. Re-created after every kill,
    because the tunnel dies with the container."""

    def __init__(self, pod: str):
        self.pod = pod
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(["kubectl", "port-forward", "-n", "linkpulse", f"pod/{self.pod}", f"{LOCAL_PORT}:8080"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=1):
                    return self
            except OSError:
                time.sleep(0.5)
        raise c.ChaosError(f"port-forward to {self.pod} never came up")

    def __exit__(self, *_):
        if self.proc:
            self.proc.kill()
            self.proc.wait()

    def leak(self, mb: int) -> int | None:
        """Returns the total leaked MB the pod reports, or None if the pod died mid-request
        (which is the expected outcome of the last leak)."""
        req = urllib.request.Request(f"http://127.0.0.1:{LOCAL_PORT}/debug/leak?mb={mb}", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                import json
                return json.load(resp).get("leakedMB")
        except (urllib.error.URLError, OSError, ValueError):
            return None


def kill_once(pod: str, run: c.Run, hold_for_alert: bool) -> None:
    """Leak the target container past its limit once. With hold_for_alert, pause at ~90% so
    LinkpulseMemoryNearLimit gets its minute."""
    before = restarts_of(pod)
    c.wait_for(run, f"{pod} container running", lambda: container_running(pod), timeout=180, interval=3)
    with Forward(pod) as fwd:
        total = fwd.leak(40)
        total = fwd.leak(40) if total is not None else None
        total = fwd.leak(20) if total is not None else None
        run.mark(f"leaked to {total} MB retained" if total else "pod died before the hold", f"limit {LIMIT_MB}Mi")
        if hold_for_alert and total:
            c.wait_for(run, "working set above 85% of the limit (Prometheus view)", lambda: mem_ratio(pod) > 0.85, timeout=120)
            c.wait_for(run, "LinkpulseMemoryNearLimit firing", lambda: c.alert_state("LinkpulseMemoryNearLimit", pod=pod) is not None, timeout=180)
        run.mark("pushing over the limit: +40 MB")
        fwd.leak(40)
    c.wait_for(run, f"{pod} restarted (restart count {before:.0f} -> {before + 1:.0f})",
               lambda: restarts_of(pod) >= before + 1, timeout=180, interval=3)
    reason = last_termination(pod)
    c.check(run, "last termination reason is OOMKilled", reason == "OOMKilled", str(reason))


def pre() -> None:
    RUN.start().phase("pre: the healthy state")
    c.require_healthy(RUN)
    ready = c.ready_app_pods()
    target = sorted(ready)[0]
    RUN.fact("targetPod", target)
    RUN.fact("otherPods", [p for p in ready if p != target])
    RUN.fact("restartsAtStart", restarts_of(target))
    # The endpoint must exist, or the experiment is testing nothing. A 400 (no mb) proves
    # it is routed without leaking anything.
    with Forward(target) as fwd:
        req = urllib.request.Request(f"http://127.0.0.1:{LOCAL_PORT}/debug/leak", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            code = 200
        except urllib.error.HTTPError as exc:
            code = exc.code
    c.check(RUN, "/debug/leak is routed on the target (400 without mb)", code == 400,
            f"HTTP {code}; is LINKPULSE_ENABLE_DEBUG_LEAK=true in the local overlay and rolled out?")
    RUN.mark(f"target: {target}; the other replica must keep serving throughout")


def during() -> None:
    RUN.load().phase("during: leak, OOMKill, repeat")
    target = RUN.data["facts"]["targetPod"]
    t0 = c.now()

    kill_once(target, RUN, hold_for_alert=True)
    RUN.fact("secondsToFirstOOMKill", round(c.now() - t0))
    t_alert, _ = c.wait_for(RUN, "LinkpulseOOMKilled firing", lambda: c.alert_state("LinkpulseOOMKilled", pod=target) is not None, timeout=180)
    RUN.fact("secondsToOOMKilledAlert", round(c.now() - t0))
    c.check(RUN, "the other replica is serving through the ingress", redirect_serves())

    # Into CrashLoopBackOff: keep killing the restarted container until the kubelet's
    # backoff is long enough for the alert's `for: 1m` to elapse.
    kills = 1
    while kills < 6:
        kill_once(target, RUN, hold_for_alert=False)
        kills += 1
        reason = waiting_reason(target)
        RUN.mark(f"after kill {kills}: waiting reason {reason}")
        if c.alert_state("LinkpulseCrashLooping", pod=target) is not None:
            break
        try:
            c.wait_for(RUN, "LinkpulseCrashLooping firing", lambda: c.alert_state("LinkpulseCrashLooping", pod=target) is not None,
                       timeout=45, interval=5)
            break
        except c.ChaosError:
            continue  # backoff too short yet; kill again
    c.check(RUN, "LinkpulseCrashLooping firing", c.alert_state("LinkpulseCrashLooping", pod=target) is not None)
    RUN.fact("killsToCrashLoopAlert", kills)
    RUN.fact("secondsToCrashLoopAlert", round(c.now() - t0))
    RUN.fact("restartsAtPeak", restarts_of(target))
    c.check(RUN, "the other replica is still serving through the ingress", redirect_serves())
    below = c.alert_state("LinkpulseBelowMinReplicas")
    RUN.fact("belowMinReplicasAlertSeen", below is not None)
    RUN.mark("recovering: no more leaks; the kubelet restarts the container on its own")


def post() -> None:
    RUN.load().phase("post: recovery")
    target = RUN.data["facts"]["targetPod"]
    t0 = c.now()
    t_run, _ = c.wait_for(RUN, f"{target} running and ready again (after the last backoff)", lambda: target in c.ready_app_pods(), timeout=400, interval=5)
    RUN.fact("secondsToRecoverAfterLastKill", round(t_run))
    c.wait_for(RUN, "LinkpulseCrashLooping resolved", lambda: c.alert_state("LinkpulseCrashLooping", pod=target) is None, timeout=300)
    c.wait_for(RUN, "LinkpulseMemoryNearLimit resolved", lambda: c.alert_state("LinkpulseMemoryNearLimit", pod=target) is None, timeout=300)
    # increase(...[5m]) keeps this one up for five minutes past the last kill, by design.
    c.wait_for(RUN, "LinkpulseOOMKilled resolved (5m window elapsed)", lambda: c.alert_state("LinkpulseOOMKilled", pod=target) is None, timeout=420, interval=10)
    c.check(RUN, "2 ready pods", len(c.ready_app_pods()) >= 2, str(c.ready_app_pods()))
    c.check(RUN, "the restart count is the evidence, not a symptom: still recorded", restarts_of(target) >= RUN.data["facts"]["restartsAtPeak"])
    RUN.finish(panels=[
        ("linkpulse-kubernetes", 13, "memory-working-set-vs-limit"),
        ("linkpulse-kubernetes", 15, "container-restarts"),
        ("linkpulse-kubernetes", 16, "waiting-reason"),
        ("linkpulse-kubernetes", 10, "pod-readiness"),
    ])


if __name__ == "__main__":
    sys.exit(c.cli({"pre": pre, "during": during, "post": post}))
