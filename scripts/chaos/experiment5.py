#!/usr/bin/env python3
"""Chaos experiment 5: a node goes away -- politely, then not.

Two halves, because Kubernetes treats them completely differently and both matter:

  drain    the voluntary disruption. `kubectl drain` asks the API to evict the pods on a
           node, and the PodDisruptionBudget (maxUnavailable: 1) is what decides whether
           it may. With two replicas on two nodes the answer is "one at a time": the pod
           is evicted, its replacement is scheduled elsewhere, and the service never has
           fewer than one ready replica. The script does this half itself, with kubectl,
           then uncordons -- so the second half starts from a node that is schedulable
           again and the two measurements do not contaminate each other.

  loss     the involuntary one. `k3d node stop` freezes the node's container (the mise
           task does it between phases). No eviction API is consulted; the kubelet just
           stops reporting. The node goes NotReady after node-monitor-grace-period (40s),
           the pods on it are marked not ready, and the Deployment does NOT replace them
           until the taint-based eviction fires at tolerationSeconds (300s) -- so the
           service runs on one replica for five minutes. That window is the number this
           half measures, and LinkpulseBelowMinReplicas is the alert that reports it.

The redirect path is checked through the ingress at every stage: one replica on a
surviving node is enough, and that is the whole reason there are two on two.

    mise run chaos-5
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import chaos as c  # noqa: E402

RUN = c.Run(5, "node loss (drain, then a stopped node)")


def pods_by_node() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in c.app_pods():
        if not p["metadata"].get("deletionTimestamp"):
            out.setdefault(p["spec"].get("nodeName", "?"), []).append(p["metadata"]["name"])
    return out


def node_ready(node: str) -> bool:
    n = c.kubectl("get", "node", node)
    return any(cd["type"] == "Ready" and cd["status"] == "True" for cd in n["status"]["conditions"])


def node_unschedulable(node: str) -> bool:
    return bool(c.kubectl("get", "node", node)["spec"].get("unschedulable"))


def pdb_allowed() -> int:
    return int(c.kubectl("get", "pdb", "linkpulse", "-n", "linkpulse")["status"].get("disruptionsAllowed", -1))


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


def singletons_on(node: str) -> list[tuple[str, str]]:
    """(namespace, pod) for every non-DaemonSet, non-application pod on the node: the
    single-replica things -- Traefik, CoreDNS, Prometheus, Alertmanager, kube-state-metrics,
    Grafana -- that would go down with it. Losing Traefik takes the ingress the checks
    use; losing Prometheus or kube-state-metrics takes the series the alerts read; the
    experiment would be measuring its own blindness."""
    out = []
    for p in c.kubectl("get", "pods", "-A", "--field-selector", f"spec.nodeName={node}")["items"]:
        ns = p["metadata"]["namespace"]
        owners = [o["kind"] for o in p["metadata"].get("ownerReferences", [])]
        if ns == "linkpulse" or "DaemonSet" in owners or "Job" in owners or p["status"].get("phase") != "Running":
            continue
        out.append((ns, p["metadata"]["name"]))
    return out


def choose_node() -> str:
    """Fixed (CHAOS_TARGET_NODE, default agent-0) rather than chosen, so the mise task's
    `k3d node stop` line can name it without shell substitution. Never the server: it is
    the control plane, and stopping it ends the cluster rather than testing it. The `pre`
    phase makes the fixed choice a fair one."""
    node = os.environ.get("CHAOS_TARGET_NODE", "k3d-linkpulse-agent-0")
    names = [n["metadata"]["name"] for n in c.kubectl("get", "nodes")["items"]]
    if node not in names or "server" in node:
        raise c.ChaosError(f"{node} is not an agent node of this cluster: {names}")
    return node


def move_singletons_off(node: str) -> list[tuple[str, str]]:
    """Cordon, delete the singletons so their controllers recreate them elsewhere, wait
    for every Deployment in their namespaces to be Available again, uncordon."""
    victims = singletons_on(node)
    if not victims:
        return []
    c.kubectl_run("cordon", node)
    for ns, pod in victims:
        c.kubectl_run("delete", "pod", pod, "-n", ns, "--wait=false")
    for ns in sorted({ns for ns, _ in victims}):
        c.kubectl_run("wait", "--for=condition=Available", "deploy", "--all", "-n", ns, "--timeout=300s")
    c.kubectl_run("uncordon", node)
    return victims


def pre() -> None:
    RUN.start().phase("pre: the healthy state")
    c.require_healthy(RUN)
    node = choose_node()
    RUN.fact("targetNode", node)
    RUN.fact("pdbDisruptionsAllowedAtStart", pdb_allowed())
    c.check(RUN, "PDB allows exactly one disruption at two replicas", pdb_allowed() == 1, str(pdb_allowed()))
    c.check(RUN, "the target node is Ready", node_ready(node))

    # Preparation, and a finding in its own right: this cluster's ingress controller,
    # DNS and the whole monitoring stack are single replicas with no PodDisruptionBudget,
    # so whichever node they sit on cannot be lost without losing them. The application
    # is what is under test, so they are moved off the target first -- and the
    # write-up says that a production cluster would have given them replicas and budgets
    # of their own, at which point this step would not exist.
    moved = move_singletons_off(node)
    RUN.fact("singletonsMovedOffTarget", moved)
    if moved:
        RUN.mark(f"moved {len(moved)} single-replica pods off {node} first", str(moved))
        c.wait_for(RUN, "observers answering again", lambda: bool(c.prom("up")) and c.alerts() is not None, timeout=180)

    # And a replica has to be on the node, or nothing is lost when it goes. Nothing moves
    # one there by itself -- the scheduler places pods at creation -- so roll the
    # deployment: the topology spread constraint re-spreads across the three nodes.
    if not pods_by_node().get(node):
        RUN.mark("no replica on the target; rolling the deployment to re-spread")
        c.kubectl_run("rollout", "restart", "deploy/linkpulse", "-n", "linkpulse")
        c.kubectl_run("rollout", "status", "deploy/linkpulse", "-n", "linkpulse", "--timeout=180s")
    c.wait_for(RUN, f"a replica on {node}, 2 ready", lambda: bool(pods_by_node().get(node)) and len(c.ready_app_pods()) >= 2,
               timeout=240, interval=3)
    RUN.fact("podsByNodeAtStart", pods_by_node())
    RUN.mark(f"target: {node}", f"hosting {pods_by_node().get(node)}")


def drain() -> None:
    RUN.load().phase("during, first half: drain (voluntary, PDB-governed)")
    node = RUN.data["facts"]["targetNode"]
    victims = pods_by_node().get(node, [])
    t0 = c.now()
    # --pod-selector: evict the application's pod through the eviction API (which is what
    # consults the PDB) and nothing else. Without it, drain would also evict whatever
    # singletons the preparation step did not move -- there should be none, but the
    # experiment is about the application's budget, not the cluster's housekeeping.
    RUN.mark(f"kubectl drain {node} --pod-selector app.kubernetes.io/name=linkpulse", f"evicting {victims}")
    out = c.kubectl_run("drain", node, "--ignore-daemonsets", "--delete-emptydir-data", "--timeout=180s",
                        "--pod-selector", "app.kubernetes.io/name=linkpulse")
    RUN.mark("drain returned", out.strip().splitlines()[-1] if out.strip() else "")
    RUN.fact("drainSeconds", round(c.now() - t0))
    c.check(RUN, "the node is cordoned", node_unschedulable(node))
    c.check(RUN, "no application pod remains on the node", not pods_by_node().get(node), str(pods_by_node()))
    t_ready, _ = c.wait_for(RUN, "2 ready replicas again, on other nodes", lambda: len(c.ready_app_pods()) >= 2, timeout=180, interval=3)
    RUN.fact("drainSecondsToTwoReady", round(c.now() - t0))
    RUN.fact("podsByNodeAfterDrain", pods_by_node())
    c.check(RUN, "redirect path served throughout (one replica was always ready)", redirect_serves())
    # The alert may or may not have caught the gap: a replacement that is ready inside
    # 30s never trips `for: 30s`. Either is a finding; record which.
    RUN.fact("belowMinReplicasDuringDrain", c.alert_state("LinkpulseBelowMinReplicas"))
    c.kubectl_run("uncordon", node)
    c.wait_for(RUN, "node uncordoned", lambda: not node_unschedulable(node), timeout=60, interval=2)

    # The second half needs a replica ON the node, and nothing moves one back by itself:
    # the scheduler places pods when they are created, not when a node reappears. A
    # rollout restart re-spreads by the topology constraint, which is the same mechanism
    # that put them on two nodes to begin with.
    RUN.mark("rolling the deployment so a replica lands back on the target")
    c.kubectl_run("rollout", "restart", "deploy/linkpulse", "-n", "linkpulse")
    c.kubectl_run("rollout", "status", "deploy/linkpulse", "-n", "linkpulse", "--timeout=180s")
    c.wait_for(RUN, f"a replica scheduled on {node}, 2 ready", lambda: bool(pods_by_node().get(node)) and len(c.ready_app_pods()) >= 2,
               timeout=240, interval=3)
    RUN.fact("podsByNodeBeforeStop", pods_by_node())
    RUN.mark(f"injecting: k3d node stop {node}")


def loss() -> None:
    RUN.load().phase("during, second half: the node is gone")
    node = RUN.data["facts"]["targetNode"]
    t0 = c.now()
    victims = pods_by_node().get(node, [])
    c.check(RUN, "a replica was on the node when it stopped", bool(victims), str(pods_by_node()))
    RUN.fact("podsOnNodeAtStop", victims)
    RUN.fact("collateralOnNodeAtStop", singletons_on(node))

    t_nr, _ = c.wait_for(RUN, f"{node} NotReady (Kubernetes view)", lambda: not node_ready(node), timeout=180, interval=3)
    t_a1, _ = c.wait_for(RUN, "KubeNodeNotReady firing", lambda: c.alert_state("KubeNodeNotReady", node=node) is not None, timeout=240)
    t_b, _ = c.wait_for(RUN, "fewer ready replicas than the HPA minimum", lambda: len(c.ready_app_pods()) < 2, timeout=180, interval=3)
    t_a2, _ = c.wait_for(RUN, "LinkpulseBelowMinReplicas firing", lambda: c.alert_state("LinkpulseBelowMinReplicas") is not None, timeout=240)
    RUN.fact("secondsToNodeNotReady", round(t_nr))
    RUN.fact("secondsToNodeAlert", round(t_nr + t_a1))
    RUN.fact("secondsToBelowMinReplicasAlert", round(t_nr + t_a1 + t_b + t_a2))
    c.check(RUN, "redirect path still served by the surviving replica", redirect_serves())

    # The long wait: taint-based eviction at tolerationSeconds=300, then a replacement.
    t_rep, _ = c.wait_for(RUN, "replacement replica ready elsewhere (2 ready again)", lambda: len(c.ready_app_pods()) >= 2,
                          timeout=600, interval=5)
    # Timed from Prometheus, not from when this script noticed: the deployment's ready
    # count as kube-state-metrics reported it, sampled every 15s across the whole phase.
    below = c.seconds_where('kube_deployment_status_replicas_ready{namespace="linkpulse", deployment="linkpulse"}',
                            t0, c.now(), lambda v: v < 2)
    RUN.fact("secondsBelowMinReplicas", round(below))
    RUN.mark(f"the deployment sat below its minimum for ~{below:.0f}s (Prometheus, 15s samples)")
    RUN.fact("podsByNodeAfterReplacement", pods_by_node())
    c.check(RUN, "redirect path served throughout", redirect_serves())
    c.wait_for(RUN, "LinkpulseBelowMinReplicas resolved", lambda: c.alert_state("LinkpulseBelowMinReplicas") is None, timeout=180)
    RUN.mark(f"recovering: k3d node start {node}")


def post() -> None:
    RUN.load().phase("post: the node returns")
    node = RUN.data["facts"]["targetNode"]
    t0 = c.now()
    t_r, _ = c.wait_for(RUN, f"{node} Ready again", lambda: node_ready(node), timeout=300, interval=3)
    RUN.fact("secondsToNodeReady", round(t_r))
    c.wait_for(RUN, "KubeNodeNotReady resolved", lambda: c.alert_state("KubeNodeNotReady", node=node) is None, timeout=300)
    c.wait_for(RUN, "the pod stranded on the node is cleaned up",
               lambda: not any(p["metadata"].get("deletionTimestamp") for p in c.app_pods()), timeout=300, interval=5)
    c.check(RUN, "2 ready replicas", len(c.ready_app_pods()) >= 2, str(c.ready_app_pods()))
    c.check(RUN, "PDB allows one disruption again", pdb_allowed() == 1, str(pdb_allowed()))
    RUN.fact("podsByNodeAtEnd", pods_by_node())
    RUN.finish(panels=[
        ("linkpulse-kubernetes", 18, "node-ready"),
        ("linkpulse-kubernetes", 8, "replicas"),
        ("linkpulse-kubernetes", 11, "pods-per-node"),
        ("linkpulse-kubernetes", 10, "pod-readiness"),
    ])


if __name__ == "__main__":
    sys.exit(c.cli({"pre": pre, "drain": drain, "loss": loss, "post": post}))
