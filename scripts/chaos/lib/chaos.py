"""Shared machinery for the chaos experiments: observe, assert, and leave evidence.

Every experiment in scripts/chaos/ is the same shape -- a `pre` phase that records the
healthy state and refuses to start from an unhealthy one, a `during` phase that waits for
the failure's signature to appear in the monitoring stack and asserts on it, and a `post`
phase that waits for recovery and writes the timeline. The injection itself (pausing
LocalStack, stopping a node, pushing a bad commit) happens BETWEEN phases, in the mise task,
because the tools that do it (docker, k3d, git) are not the tools that observe it.

What is observed is always the monitoring stack, never the thing under test directly:
Prometheus for series, Alertmanager for alerts, kubectl for the Kubernetes objects the
rules read. If an experiment's failure is real but the stack does not see it, the
experiment FAILS -- that is the point. The alerts are the deliverable; the chaos is how
they are exercised.

Evidence goes to docs/evidence/chaos-<n>/: a run.json that accumulates across the phases
(each phase is a separate process), a timeline.md written at the end, and the Grafana
panels that show the incident, rendered to PNG through the image renderer for the window
the run covered. That directory is committed; it is the case study's raw material.

Runs inside the ops container (python + kubectl), reaching every service through the
Traefik ingress by container name with a Host header per service -- inside a container
localtest.me resolves to the container's own loopback (observability-check.py has the
full note).
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

INGRESS = os.environ.get("CHAOS_INGRESS", "http://k3d-linkpulse-serverlb")
DOMAIN = os.environ.get("CHAOS_DOMAIN", "localtest.me")
EVIDENCE_ROOT = os.environ.get("CHAOS_EVIDENCE", "docs/evidence")
TIMEOUT = 20


class ChaosError(Exception):
    """A failed assertion or a timeout. The message is what the operator reads."""


def now() -> float:
    return time.time()


def stamp(t: float | None = None) -> str:
    return datetime.fromtimestamp(t or now(), tz=timezone.utc).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------------------
# HTTP through the ingress
# ---------------------------------------------------------------------------------------


def http(method: str, host: str, path: str, body: dict | None = None, raw: bool = False, timeout: int = TIMEOUT):
    """One request to <INGRESS><path> with Host: <host>.<DOMAIN>. JSON in, JSON out."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Host": f"{host}.{DOMAIN}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(INGRESS + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload or b"null")
    except urllib.error.HTTPError as exc:
        # A 503 here is Traefik with no endpoint behind the Host: the observer itself is
        # down or moving. That is a fact about the experiment, so it surfaces as one.
        raise ChaosError(f"{host}: HTTP {exc.code} for {path}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ChaosError(f"{host}: {exc}") from exc


def prom(expr: str, at: float | None = None) -> list[tuple[dict, float]]:
    """Instant query. Returns [(labels, value)]; empty when the expression has no data."""
    params = {"query": expr}
    if at:
        params["time"] = str(at)
    out = http("GET", "prometheus", "/api/v1/query?" + urllib.parse.urlencode(params))
    if out.get("status") != "success":
        raise ChaosError(f"prometheus rejected {expr!r}: {out}")
    return [(r["metric"], float(r["value"][1])) for r in out["data"]["result"]]


def prom_range(expr: str, start: float, end: float, step: int = 15) -> list[tuple[dict, list[tuple[float, float]]]]:
    """Range query. Returns [(labels, [(t, value), ...])]."""
    params = {"query": expr, "start": str(start), "end": str(end), "step": str(step)}
    out = http("GET", "prometheus", "/api/v1/query_range?" + urllib.parse.urlencode(params))
    if out.get("status") != "success":
        raise ChaosError(f"prometheus rejected {expr!r}: {out}")
    return [(r["metric"], [(float(t), float(v)) for t, v in r["values"]]) for r in out["data"]["result"]]


def seconds_where(expr: str, start: float, end: float, predicate, step: int = 15) -> float:
    """How many seconds, between start and end, the (single) series satisfied predicate --
    measured from Prometheus after the fact, which is the honest way to time a condition
    the script only noticed some seconds after it began."""
    rows = prom_range(expr, start, end, step)
    if not rows:
        return 0.0
    if len(rows) > 1:
        raise ChaosError(f"{expr!r} returned {len(rows)} series, wanted one")
    return float(sum(step for _, v in rows[0][1] if predicate(v)))


def prom_scalar(expr: str, default: float | None = None) -> float | None:
    rows = prom(expr)
    if not rows:
        return default
    if len(rows) > 1:
        raise ChaosError(f"{expr!r} returned {len(rows)} series, wanted one: {[r[0] for r in rows]}")
    return rows[0][1]


def alerts() -> list[dict]:
    """Every alert Alertmanager currently holds, from its own API rather than Prometheus':
    an alert Prometheus is firing that Alertmanager never received would be a routing bug,
    and this is the side of it that reaches a human."""
    return http("GET", "alertmanager", "/api/v2/alerts?active=true&silenced=false&inhibited=true")


def alert_state(name: str, **labels: str) -> str | None:
    """'firing', 'inhibited' or None, for the alert with this name and these labels."""
    for a in alerts():
        lab = a.get("labels", {})
        if lab.get("alertname") != name or any(lab.get(k) != v for k, v in labels.items()):
            continue
        if a.get("status", {}).get("inhibitedBy"):
            return "inhibited"
        if a.get("status", {}).get("state") == "active":
            return "firing"
    return None


def kubectl(*args: str) -> dict:
    """`kubectl ... -o json`, parsed. Raises with kubectl's stderr on failure."""
    proc = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise ChaosError(f"kubectl {' '.join(args)}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def kubectl_run(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise ChaosError(f"kubectl {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def app_pods() -> list[dict]:
    return kubectl("get", "pods", "-n", "linkpulse", "-l", "app.kubernetes.io/name=linkpulse")["items"]


def ready_app_pods() -> list[str]:
    out = []
    for p in app_pods():
        conds = {c["type"]: c["status"] for c in p.get("status", {}).get("conditions", [])}
        if conds.get("Ready") == "True" and not p["metadata"].get("deletionTimestamp"):
            out.append(p["metadata"]["name"])
    return out


def argocd_app(name: str) -> dict:
    a = kubectl("get", "application", name, "-n", "argocd")
    st = a.get("status", {})
    return {
        "sync": st.get("sync", {}).get("status"),
        "health": st.get("health", {}).get("status"),
        "revision": (st.get("sync", {}).get("revision") or "")[:7],
    }


# ---------------------------------------------------------------------------------------
# Waiting and asserting
# ---------------------------------------------------------------------------------------


def wait_for(run: "Run", label: str, predicate, timeout: float, interval: float = 5.0):
    """Poll until predicate() is truthy. Records how long it took as a timeline event and
    returns (seconds, value). Raises ChaosError on timeout -- a signature that does not
    appear inside the window IS the failure this script exists to catch."""
    started = now()
    last_err = None
    while True:
        try:
            value = predicate()
        except (ChaosError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            # A stack that is briefly unreachable mid-experiment is part of the experiment
            # (experiment 5 takes a node away); keep polling, but remember why.
            value, last_err = None, exc
        if value:
            elapsed = now() - started
            run.mark(label, f"after {elapsed:.0f}s", value=value if not isinstance(value, bool) else None)
            return elapsed, value
        if now() - started > timeout:
            run.mark(f"TIMEOUT waiting for {label}", f"{timeout:.0f}s" + (f"; last error: {last_err}" if last_err else ""))
            raise ChaosError(f"timed out after {timeout:.0f}s waiting for {label}")
        time.sleep(interval)


def check(run: "Run", label: str, condition: bool, detail: str = "") -> None:
    if condition:
        run.mark(f"ok: {label}", detail)
        return
    run.mark(f"FAIL: {label}", detail)
    raise ChaosError(f"{label}{': ' + detail if detail else ''}")


# ---------------------------------------------------------------------------------------
# The run: timeline across phases, and evidence
# ---------------------------------------------------------------------------------------


class Run:
    """State for one experiment run, persisted between the pre / during / post processes."""

    def __init__(self, number: int, title: str):
        self.number = number
        self.title = title
        self.dir = os.path.join(EVIDENCE_ROOT, f"chaos-{number}")
        self.path = os.path.join(self.dir, "run.json")
        self.data: dict = {"experiment": number, "title": title, "events": [], "facts": {}}

    # -- persistence ------------------------------------------------------------------

    def start(self) -> "Run":
        os.makedirs(self.dir, exist_ok=True)
        self.data["startedAt"] = now()
        self.data["startedIso"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save()
        return self

    def load(self) -> "Run":
        if not os.path.exists(self.path):
            raise ChaosError(f"{self.path} does not exist: run the `pre` phase first")
        with open(self.path, encoding="utf-8") as fh:
            self.data = json.load(fh)
        return self

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, default=str)
            fh.write("\n")

    # -- recording --------------------------------------------------------------------

    def mark(self, label: str, detail: str = "", value=None) -> None:
        t = now()
        rel = t - self.data.get("startedAt", t)
        self.data["events"].append({"t": t, "rel": round(rel), "label": label, "detail": detail, "value": value})
        self._save()
        print(f"  T+{rel:>4.0f}s  {label}{'  -- ' + detail if detail else ''}")

    def fact(self, key: str, value) -> None:
        self.data["facts"][key] = value
        self._save()

    def phase(self, name: str) -> None:
        self.data.setdefault("phases", []).append({"name": name, "t": now()})
        self._save()
        print(f"\n== experiment {self.number}: {self.title} -- {name}  [{stamp()}]")

    # -- evidence ---------------------------------------------------------------------

    def render(self, dashboard: str, panel: int, name: str, pad_s: int = 60) -> str | None:
        """Render one Grafana panel for the run's window to <dir>/<name>.png. Returns the
        path, or None if the renderer refused -- the timeline is the evidence of record,
        the PNGs are illustration, so a failed render is reported, not fatal."""
        start = int((self.data["startedAt"] - pad_s) * 1000)
        end = int((now() + pad_s) * 1000)
        path = f"/render/d-solo/{dashboard}/?panelId={panel}&width=900&height=360&from={start}&to={end}&tz=UTC&theme=light"
        target = os.path.join(self.dir, f"{name}.png")
        png = None
        for attempt in range(6):
            try:
                png = http("GET", "grafana", path, raw=True, timeout=90)
                break
            except ChaosError as exc:
                # Grafana or the renderer may be rescheduling after the experiment itself.
                if attempt == 5:
                    print(f"  (render {name}: {exc})")
                    return None
                time.sleep(10)
        if not png.startswith(b"\x89PNG"):
            print(f"  (render {name}: not a PNG, {len(png)} bytes)")
            return None
        with open(target, "wb") as fh:
            fh.write(png)
        print(f"  rendered {target} ({len(png)} bytes)")
        return target

    def finish(self, panels: list[tuple[str, int, str]] = ()) -> None:
        self.data["finishedAt"] = now()
        self.data["durationS"] = round(self.data["finishedAt"] - self.data["startedAt"])
        self._save()
        rendered = [p for p in (self.render(d, i, n) for d, i, n in panels) if p]
        self._write_markdown(rendered)

    def _write_markdown(self, rendered: list[str]) -> None:
        lines = [
            f"# Chaos experiment {self.number}: {self.title}",
            "",
            f"Run started {self.data['startedIso']}, {self.data['durationS']}s end to end. "
            "Generated by `scripts/chaos/experiment{}.py`; every line below is an observation the ".format(self.number)
            + "script made through Prometheus, Alertmanager or the Kubernetes API, not a description.",
            "",
            "## Timeline",
            "",
            "| T+ | event | detail |",
            "|---:|---|---|",
        ]
        for e in self.data["events"]:
            label = e["label"].replace("|", "/")
            detail = (e["detail"] or "").replace("|", "/")
            if label.startswith("FAIL") or label.startswith("TIMEOUT"):
                label = f"**{label}**"
            lines.append(f"| {e['rel']}s | {label} | {detail} |")
        if self.data["facts"]:
            lines += ["", "## Facts", ""]
            for k, v in self.data["facts"].items():
                lines.append(f"- **{k}**: {v}")
        if rendered:
            lines += ["", "## Panels", ""]
            for p in rendered:
                lines.append(f"![{os.path.basename(p)}]({os.path.basename(p)})")
        lines.append("")
        with open(os.path.join(self.dir, "timeline.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print(f"\n  timeline: {os.path.join(self.dir, 'timeline.md')}")


# ---------------------------------------------------------------------------------------
# Preconditions shared by every experiment
# ---------------------------------------------------------------------------------------


def require_healthy(run: Run, expect_ready: int = 2) -> None:
    """An experiment started from a broken cluster proves nothing about the break it
    introduces. Refuse to start unless the things it will assert on are currently right."""
    ready = ready_app_pods()
    check(run, f"{expect_ready} ready application pods", len(ready) >= expect_ready, f"ready: {ready}")
    firing = sorted({a["labels"]["alertname"] for a in alerts() if a["labels"].get("severity") == "critical"})
    check(run, "no critical alert firing", not firing, str(firing))
    up = prom_scalar('count(up{job="kubernetes-pods", app="linkpulse"} == 1)', 0)
    check(run, "prometheus is scraping the application", (up or 0) >= 1, f"{up} targets")
    run.fact("readyPodsAtStart", ready)


def cli(main_by_phase: dict[str, "callable"]) -> int:
    """`experimentN.py <phase>`: dispatch, and turn a ChaosError into a clean exit 1."""
    phases = list(main_by_phase)
    if len(sys.argv) != 2 or sys.argv[1] not in phases:
        print(f"usage: {sys.argv[0]} {{{'|'.join(phases)}}}", file=sys.stderr)
        return 2
    try:
        main_by_phase[sys.argv[1]]()
        return 0
    except ChaosError as exc:
        print(f"\nchaos: FAIL: {exc}", file=sys.stderr)
        return 1
