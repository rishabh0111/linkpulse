#!/usr/bin/env python3
"""What the account actually cost, by day and by service, next to the budgets that guard it.

The case study needs the number, the burst runbook needs it every morning of the window,
and the always-free claim needs it to be $0.00 the rest of the time. This reads Cost
Explorer for a date range, prints a table (day x service, with totals) and, beside it,
every AWS Budget in the account with its limit and actual spend -- so the $1 tripwire
and the $25 burst ceiling from envs/aws/guardrails.tf are read back from the same place
they alert from.

Two things worth knowing before running it:

  1. The Cost Explorer API costs $0.01 per request. This script makes two (one grouped by
     service, one by the Environment tag) plus one free Budgets call, so a run is $0.02
     -- which will itself appear in the report, under "AWS Cost Explorer". Run it once a
     day during the burst, not in a loop.
  2. Every figure is gross: credits and refunds are filtered out, so on an account
     spending its sign-up credits this still shows what the burst consumed.
  3. Cost Explorer data lags by up to 24 hours, and tag-grouped cost is empty until the
     tag has been activated as a cost allocation tag in the Billing console. That is a
     console action Terraform cannot take; the burst runbook has it as a step, and until
     it has been done the by-environment section prints as such rather than as zeros.

Runs in the ops container with the operator's credentials (`ce:GetCostAndUsage`,
`budgets:ViewBudget`, `sts:GetCallerIdentity`). Cost Explorer has no LocalStack, so
this is validated by running it against a real account, and nothing here has been
until the burst -- the tracker says so.

    python3 scripts/cost-report.py                       # last 7 days
    python3 scripts/cost-report.py --days 3 --out docs/evidence/cost/2026-xx-xx.md
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import botocore.exceptions
import botocore.session


def fmt(amount: float) -> str:
    return f"${amount:,.2f}"


def cost_by(ce, start: str, end: str, group: dict) -> tuple[dict, dict]:
    """Daily cost grouped by one dimension. Returns (rows[day][key] -> usd, totals[key])."""
    rows: dict[str, dict[str, float]] = defaultdict(dict)
    totals: dict[str, float] = defaultdict(float)
    token = None
    while True:
        kwargs = dict(
            TimePeriod={"Start": start, "End": end},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[group],
            # Gross cost. Credits and refunds are line items of their own with negative
            # amounts, and on an account running on its sign-up credits they net every
            # service to $0.00 -- the same reason the budgets set include_credit = false.
            Filter={"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}},
        )
        if token:
            kwargs["NextPageToken"] = token
        resp = ce.get_cost_and_usage(**kwargs)
        for day in resp["ResultsByTime"]:
            d = day["TimePeriod"]["Start"]
            for g in day["Groups"]:
                key = g["Keys"][0]
                usd = float(g["Metrics"]["UnblendedCost"]["Amount"])
                if abs(usd) < 0.000001:
                    continue
                rows[d][key] = rows[d].get(key, 0.0) + usd
                totals[key] += usd
        token = resp.get("NextPageToken")
        if not token:
            break
    return rows, totals


def table(rows: dict, totals: dict, label: str, days: list[str]) -> list[str]:
    cols = sorted(totals, key=lambda k: -totals[k])
    if not cols:
        return [f"_No cost recorded by {label} in this period._", ""]
    out = ["| day | " + " | ".join(c.replace("$", "").replace("|", "/") or "(untagged)" for c in cols) + " | total |"]
    out.append("|---|" + "---:|" * (len(cols) + 1))
    for d in days:
        r = rows.get(d, {})
        out.append(f"| {d} | " + " | ".join(fmt(r.get(c, 0.0)) for c in cols) + f" | {fmt(sum(r.values()))} |")
    out.append("| **total** | " + " | ".join(f"**{fmt(totals[c])}**" for c in cols) + f" | **{fmt(sum(totals.values()))}** |")
    out.append("")
    return out


def budgets_section(session, account: str) -> list[str]:
    budgets = session.create_client("budgets", region_name="us-east-1")  # a global service; the endpoint is us-east-1
    out = ["## Budgets", "", "| budget | limit | actual so far | forecast | state |", "|---|---:|---:|---:|---|"]
    resp = budgets.describe_budgets(AccountId=account)
    for b in resp.get("Budgets", []):
        limit = float(b["BudgetLimit"]["Amount"])
        spend = b.get("CalculatedSpend", {})
        actual = float(spend.get("ActualSpend", {}).get("Amount", 0))
        forecast = spend.get("ForecastedSpend", {}).get("Amount")
        state = "OVER" if actual > limit else ("warm" if actual > limit * 0.5 else "ok")
        out.append(
            f"| {b['BudgetName']} | {fmt(limit)} | {fmt(actual)} | "
            f"{fmt(float(forecast)) if forecast else '-'} | {state} |"
        )
    if len(out) == 5:
        out.append("| _none_ | | | | |")
    out.append("")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="how many days back from today")
    parser.add_argument("--start", help="YYYY-MM-DD, overrides --days")
    parser.add_argument("--end", help="YYYY-MM-DD, exclusive; default today+1 so today is included")
    parser.add_argument("--out", help="also write the report here (markdown)")
    parser.add_argument("--region", default=None, help="only for STS; Cost Explorer and Budgets are global")
    args = parser.parse_args()

    today = date.today()
    end = args.end or (today + timedelta(days=1)).isoformat()
    start = args.start or (today - timedelta(days=args.days - 1)).isoformat()
    days = []
    d = date.fromisoformat(start)
    while d.isoformat() < end:
        days.append(d.isoformat())
        d += timedelta(days=1)

    session = botocore.session.get_session()
    region = args.region or os.environ.get("AWS_REGION") or "us-east-1"
    sts = session.create_client("sts", region_name=region)
    ident = sts.get_caller_identity()
    account = ident["Account"]
    ce = session.create_client("ce", region_name="us-east-1")  # Cost Explorer is only served from us-east-1

    lines = [f"# Cost report: {start} to {end} (exclusive)", "", f"Account `{account}`, generated {today.isoformat()}. "
             "Unblended cost, USD. Cost Explorer lags up to 24h; the last day is partial.", ""]

    by_service, svc_totals = cost_by(ce, start, end, {"Type": "DIMENSION", "Key": "SERVICE"})
    lines += ["## By service", ""] + table(by_service, svc_totals, "service", days)

    by_env, env_totals = cost_by(ce, start, end, {"Type": "TAG", "Key": "Environment"})
    lines += ["## By `Environment` tag", ""]
    if env_totals and set(env_totals) != {""}:
        lines += table({d: {k.replace("Environment$", ""): v for k, v in r.items()} for d, r in by_env.items()},
                       {k.replace("Environment$", ""): v for k, v in env_totals.items()}, "tag", days)
    else:
        lines += ["_Nothing is attributed to the tag. Either nothing was billed, or `Environment` has not "
                  "been activated as a cost allocation tag in the Billing console (a manual step; "
                  "activation applies from that point on, not retroactively)._", ""]

    lines += budgets_section(session, account)
    lines += [f"_This report cost $0.02 in Cost Explorer API calls._", ""]

    text = "\n".join(lines)
    print(text)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except botocore.exceptions.ClientError as exc:
        err = exc.response.get("Error", {})
        print(f"cost-report: {err.get('Code')}: {err.get('Message')}", file=sys.stderr)
        sys.exit(2)
    except botocore.exceptions.BotoCoreError as exc:
        print(f"cost-report: {exc}", file=sys.stderr)
        sys.exit(2)
