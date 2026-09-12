#!/usr/bin/env python3
"""Print the one line the tracker quotes from a k6 --summary-export JSON, and exit 1 if any
threshold in it failed -- so a `mise run k6-baseline` is a regression test, not a report.

    python3 scripts/k6-summary.py load-testing/k6/output/baseline.json
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: k6-summary.py <summary-export.json>", file=sys.stderr)
        return 2
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    m = data["metrics"]

    def v(name: str, key: str, default=float("nan")):
        return m.get(name, {}).get(key, default)

    parts = [f"requests={int(v('http_reqs', 'count', 0))}"]
    if "linkpulse_redirect_duration" in m:
        parts.append(
            f"redirect p50={v('linkpulse_redirect_duration', 'med'):.1f}ms "
            f"p95={v('linkpulse_redirect_duration', 'p(95)'):.1f}ms "
            f"p99={v('linkpulse_redirect_duration', 'p(99)'):.1f}ms"
        )
    if "linkpulse_graphql_duration" in m:
        parts.append(f"graphql p95={v('linkpulse_graphql_duration', 'p(95)'):.1f}ms")
    parts.append(f"failed={v('http_req_failed', 'value', 0) * 100:.2f}%")
    parts.append(f"dropped_iterations={int(v('dropped_iterations', 'count', 0))}")
    parts.append(f"not_302={int(v('linkpulse_redirect_not_302', 'count', 0))}")

    # In the export, a threshold's value is true when it FAILED.
    failed = [f"{name}: {t}" for name, met in m.items() for t, broken in (met.get("thresholds") or {}).items() if broken]
    print("k6: " + " | ".join(parts))
    if failed:
        print("k6: thresholds FAILED: " + "; ".join(failed))
        return 1
    print("k6: all thresholds met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
