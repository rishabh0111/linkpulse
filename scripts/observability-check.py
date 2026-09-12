#!/usr/bin/env python3
"""Assert the monitoring stack is not merely running but *observing*.

Every check here is one a chaos experiment depends on: that Prometheus is actually
scraping the application (not just up), that the alert rules loaded without error, that
Alertmanager has the receiver, that Grafana can reach Prometheus, that Loki has the
application's logs, and that a panel renders to a PNG. A stack where any of these is
false looks healthy in `kubectl get pods` and produces no evidence.

What this does NOT prove: that a rule can ever fire. "Loaded and healthy" means the
expression evaluates without error, which an expression that matches nothing does
perfectly -- two rules in this repository passed here for two phases in exactly that
state (docs/postmortem.md). The chaos experiments in scripts/chaos/ are the check for
that; this script is the check that the stack they observe through is standing.

Standard library only, like smoke.py, so it runs in a bare python image.

    python3 scripts/observability-check.py --ingress http://k3d-linkpulse-serverlb
    python3 scripts/observability-check.py --ingress http://localhost:8088

The ingress is reached by one address with a Host header per service, because inside a
container localtest.me resolves to that container's own loopback.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20

EXPECTED_JOBS = {
    "kubernetes-pods": 2,      # the two application replicas
    "kube-state-metrics": 1,
    "node-exporter": 3,        # one per node
    "kubernetes-cadvisor": 3,  # one per node
    "prometheus": 1,
    "alertmanager": 1,
    "grafana": 1,
    "loki": 1,
}

EXPECTED_ALERTS = [
    "LinkpulseHighErrorRate", "LinkpulseRedirectLatencyHigh", "LinkpulseTargetDown",
    "LinkpulseClicksDropped", "LinkpulseDynamoThrottled",
    "LinkpulseRolloutStalled",
    "LinkpulseNotReady", "LinkpulseNoReadyPods",
    "LinkpulseCrashLooping", "LinkpulseOOMKilled", "LinkpulseMemoryNearLimit",
    "KubeNodeNotReady", "LinkpulseBelowMinReplicas",
]

# Series the dashboards and the experiment assertions read. Each must return at least
# one sample right now, or the panel would be "No data" during the incident.
EXPECTED_SERIES = [
    'linkpulse_http_requests_total',
    'linkpulse_clicks_dropped_total{reason="throttled"}',
    'linkpulse_click_shards',
    'linkpulse_ready',
    'kube_deployment_status_replicas_ready{namespace="linkpulse",deployment="linkpulse"}',
    'kube_deployment_status_condition{namespace="linkpulse",deployment="linkpulse",condition="Progressing"}',
    'kube_pod_container_status_restarts_total{namespace="linkpulse"}',
    'kube_horizontalpodautoscaler_spec_min_replicas{namespace="linkpulse"}',
    'kube_node_status_condition{condition="Ready",status="true"}',
    'container_memory_working_set_bytes{namespace="linkpulse",container="api"}',
    'kube_pod_container_resource_limits{namespace="linkpulse",container="api",resource="memory"}',
    'node_memory_MemAvailable_bytes',
]


class CheckError(Exception):
    pass


class Client:
    def __init__(self, ingress: str, domain: str):
        self.ingress = ingress.rstrip("/")
        self.domain = domain

    def get(self, service: str, path: str, raw: bool = False):
        req = urllib.request.Request(self.ingress + path, headers={"Host": f"{service}.{self.domain}"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                ctype = resp.headers.get("content-type", "")
                return resp.status, ctype, body if raw else body.decode()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("content-type", ""), err.read() if raw else err.read().decode()
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise CheckError(f"{service}{path}: {err}") from err

    def json(self, service: str, path: str):
        status, _, body = self.get(service, path)
        if status != 200:
            raise CheckError(f"{service}{path} -> HTTP {status}: {body[:200]}")
        return json.loads(body)

    def promql(self, expr: str):
        data = self.json("prometheus", "/api/v1/query?" + urllib.parse.urlencode({"query": expr}))
        if data.get("status") != "success":
            raise CheckError(f"query failed: {expr}: {data}")
        return data["data"]["result"]


passed = 0
failed = 0


def check(name: str, fn):
    global passed, failed
    try:
        detail = fn()
        passed += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    except (CheckError, AssertionError, KeyError, ValueError, json.JSONDecodeError) as err:
        failed += 1
        print(f"  FAIL  {name}: {err}")


def retry(fn, attempts: int, delay: float):
    """Some facts take a scrape interval or two to become true after a deploy."""
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except (CheckError, AssertionError, KeyError) as err:
            last = err
            time.sleep(delay)
    raise last  # type: ignore[misc]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingress", default="http://localhost:8088")
    parser.add_argument("--domain", default="localtest.me")
    parser.add_argument("--patience", type=int, default=12, help="retries for facts that need a scrape")
    args = parser.parse_args()
    c = Client(args.ingress, args.domain)

    print("Prometheus")

    def prom_ready():
        status, _, _ = c.get("prometheus", "/-/ready")
        assert status == 200, f"/-/ready -> {status}"
    check("ready", prom_ready)

    def targets():
        data = c.json("prometheus", "/api/v1/targets?state=active")["data"]["activeTargets"]
        up = {}
        down = []
        for t in data:
            job = t["labels"].get("job")
            if t["health"] == "up":
                up[job] = up.get(job, 0) + 1
            else:
                down.append(f'{job}/{t["labels"].get("instance")}: {t.get("lastError", "")[:80]}')
        missing = {j: (n, up.get(j, 0)) for j, n in EXPECTED_JOBS.items() if up.get(j, 0) < n}
        assert not missing, f"targets below expected count (want, have): {missing}; down: {down[:5]}"
        return ", ".join(f"{j}={n}" for j, n in sorted(up.items()))
    check("every expected job has its targets up", lambda: retry(targets, args.patience, 5))

    def rules():
        groups = c.json("prometheus", "/api/v1/rules")["data"]["groups"]
        seen, bad = set(), []
        for g in groups:
            for r in g["rules"]:
                if r["type"] == "alerting":
                    seen.add(r["name"])
                    if r.get("health") != "ok":
                        bad.append(f'{r["name"]}: {r.get("lastError")}')
        missing = [a for a in EXPECTED_ALERTS if a not in seen]
        assert not missing, f"alert rules not loaded: {missing}"
        assert not bad, f"rules with errors: {bad}"
        return f"{len(seen)} alerting rules, all healthy"
    check("alert rules loaded and evaluating", rules)

    for expr in EXPECTED_SERIES:
        check(f"series present: {expr}", lambda e=expr: retry(lambda: (
            (lambda r: (assert_(len(r) > 0, "no samples"), f"{len(r)} series")[1])(c.promql(e))
        ), args.patience, 5))

    def two_ready():
        r = c.promql('kube_deployment_status_replicas_ready{namespace="linkpulse",deployment="linkpulse"}')
        assert r and float(r[0]["value"][1]) >= 2, f"ready replicas: {r}"
    check("kube-state-metrics sees >= 2 ready application replicas", lambda: retry(two_ready, args.patience, 5))

    def no_firing():
        r = c.promql('ALERTS{alertstate="firing", severity="critical"}')
        names = sorted({x["metric"]["alertname"] for x in r})
        assert not names, f"critical alerts firing on a healthy cluster: {names}"
    check("no critical alert firing at rest", no_firing)

    print("Alertmanager")

    def am_ready():
        status, _, _ = c.get("alertmanager", "/-/ready")
        assert status == 200
    check("ready", am_ready)

    def am_receiver():
        data = c.json("alertmanager", "/api/v2/status")
        cfg = data["config"]["original"]
        assert "discord_configs" in cfg, "discord receiver not in loaded config"
        assert "webhook_url_file" in cfg, "webhook URL should come from a file, not the config"
        assert "discord.com" not in cfg, "a webhook URL is inlined in the config"
    check("discord receiver configured, URL from file", am_receiver)

    def am_alerts_api():
        data = c.json("alertmanager", "/api/v2/alerts")
        assert isinstance(data, list)
        return f"{len(data)} active"
    check("/api/v2/alerts answers (the chaos assertions read this)", am_alerts_api)

    print("Grafana")

    def gf_health():
        data = c.json("grafana", "/api/health")
        assert data.get("database") == "ok", data
        return data.get("version")
    check("healthy", gf_health)

    for uid in ("linkpulse-overview", "linkpulse-kubernetes", "linkpulse-logs"):
        def dash(u=uid):
            data = c.json("grafana", f"/api/dashboards/uid/{u}")
            # Not meta.provisioned: with allowUiUpdates=true Grafana reports that as
            # false on purpose, so the UI permits saving. The provider's folder is the
            # fact that proves the file was the source.
            assert data["meta"].get("folderTitle") == "LinkPulse", f'folder={data["meta"].get("folderTitle")!r}'
            return f'{len(data["dashboard"]["panels"])} panels'
        check(f"dashboard provisioned: {uid}", dash)

    def ds_health(uid):
        data = c.json("grafana", f"/api/datasources/uid/{uid}/health")
        assert data.get("status") == "OK", data
    for uid in ("prometheus", "loki"):
        check(f"datasource reachable: {uid}", lambda u=uid: ds_health(u))

    def render():
        status, ctype, body = c.get(
            "grafana",
            "/render/d-solo/linkpulse-overview/?panelId=1&width=480&height=240&from=now-15m&to=now&tz=UTC",
            raw=True)
        assert status == 200, f"HTTP {status}: {body[:200]!r}"
        assert body[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG (content-type {ctype})"
        return f"{len(body)} bytes"
    check("panel renders to PNG through the image renderer", lambda: retry(render, 6, 10))

    print("Loki")

    def loki_ready():
        status, _, body = c.get("grafana", "/api/datasources/proxy/uid/loki/ready")
        assert status == 200, f"{status}: {body[:100]}"
    check("ready (via Grafana proxy)", loki_ready)

    def loki_logs():
        now = time.time_ns()
        q = urllib.parse.urlencode({
            "query": '{app="linkpulse"} | json | level!=""',
            "start": str(now - 30 * 60 * 10**9), "end": str(now), "limit": "5",
        })
        data = c.json("grafana", "/api/datasources/proxy/uid/loki/loki/api/v1/query_range?" + q)
        streams = data["data"]["result"]
        assert streams, "no application log streams in the last 30m"
        n = sum(len(s["values"]) for s in streams)
        return f"{len(streams)} streams, {n} lines sampled"
    check("application logs are queryable by label and JSON field", lambda: retry(loki_logs, args.patience, 5))

    print()
    print(f"observability-check: {'PASS' if failed == 0 else 'FAIL'} ({passed} ok, {failed} failed)")
    return 0 if failed == 0 else 1


def assert_(cond, msg):
    if not cond:
        raise AssertionError(msg)


if __name__ == "__main__":
    sys.exit(main())
