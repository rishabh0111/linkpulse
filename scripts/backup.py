#!/usr/bin/env python3
"""Back up and restore the LinkPulse table at zero cost, and prove the restore is faithful.

The table's point-in-time recovery is off (infra/terraform/modules/dynamodb-table: PITR
bills per GB-month and is not in the always-free tier). This script is what makes that a
decision rather than a gap: a full export before anything irreversible -- the end of the
burst window, a `terraform destroy`, an experiment that might corrupt the table -- and a
restore that puts every item back byte for byte.

    export   paginated Scan, items written in DynamoDB's own typed JSON, one per line,
             gzip-compressed, with a manifest beside it (count, key schema, capacity, time)
    restore  BatchWriteItem in pages of 25, unprocessed items retried with backoff
    verify   Scan the table and compare it with the file: missing, changed, extra

Both directions are PACED. The table is provisioned at 25 read and 25 write units --
the always-free ceiling, and the ceiling chaos experiment 1 exists to hit. An unpaced
Scan of a table with a few thousand click items would consume the read allowance in one
burst and throttle the application it is meant to be protecting; an unpaced restore
would be experiment 1 by accident. --rcu and --wcu are the budget this script keeps
under, measured from the ConsumedCapacity every response reports rather than estimated.

Runs in the ops container (docker-compose.yml), whose Python ships botocore because the
AWS CLI is built on it. boto3 is a thin layer over exactly this client API, so the calls
below read the same as they would with boto3, without a pip install step in a pinned
image. Credentials come from the environment (the ops service passes AWS_* through);
--endpoint-url points it at LocalStack, where `mise run backup-check` proves the round
trip on every dev run.

Note on permissions: the application's IAM policy explicitly DENIES Scan and BatchWrite
(modules/iam/main.tf). This runs as the operator, never as the application, and that is
the point of the deny -- a backup is an operator's action, not something the API's
credentials should be able to do.

    python3 scripts/backup.py export  --table linkpulse --out backups/linkpulse.jsonl.gz
    python3 scripts/backup.py restore --table linkpulse --in  backups/linkpulse.jsonl.gz
    python3 scripts/backup.py verify  --table linkpulse --in  backups/linkpulse.jsonl.gz
    python3 scripts/backup.py export  --table linkpulse --out backups/x.jsonl.gz --endpoint-url http://localstack:4566
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

import botocore.config
import botocore.exceptions
import botocore.session

BATCH = 25  # BatchWriteItem's hard limit per request
META_KEY = "_manifest"


# ---------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------


def client(args: argparse.Namespace):
    session = botocore.session.get_session()
    cfg = botocore.config.Config(
        # `adaptive` is the SDK's client-side rate limiter: on a throttle it backs off AND
        # slows subsequent requests, rather than retrying at the same rate. Combined with
        # the explicit pacing below it means a throttle is a symptom to log, not a loop.
        retries={"max_attempts": 10, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=60,
    )
    region = args.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        sys.exit("backup: no region: pass --region or set AWS_REGION")
    return session.create_client("dynamodb", region_name=region, endpoint_url=args.endpoint_url, config=cfg)


class Pacer:
    """Keep consumed capacity under a per-second budget.

    Every DynamoDB response carries the units the request actually consumed. After each
    one, sleep for as long as that consumption was 'worth' at the budget rate, so that
    over any window the average stays at or under it. Simple, and exact for the thing
    that matters: it uses the service's own accounting rather than a guess at item sizes.
    """

    def __init__(self, units_per_second: float):
        self.budget = units_per_second
        self.consumed = 0.0

    def account(self, response: dict) -> None:
        consumed = response.get("ConsumedCapacity") or {}
        if isinstance(consumed, list):  # BatchWriteItem reports one entry per table
            used = sum(float(c.get("CapacityUnits") or 0.0) for c in consumed)
        else:
            used = float(consumed.get("CapacityUnits") or 0.0)
        self.consumed += used
        if used and self.budget > 0:
            time.sleep(used / self.budget)


# ---------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------


def do_export(args: argparse.Namespace) -> int:
    ddb = client(args)
    desc = ddb.describe_table(TableName=args.table)["Table"]
    pacer = Pacer(args.rcu)
    started = datetime.now(timezone.utc)
    if not args.out:
        args.out = f"backups/{args.table}-{started:%Y%m%dT%H%M%SZ}.jsonl.gz"

    manifest = {
        "table": args.table,
        "startedAt": started.isoformat(),
        "keySchema": desc["KeySchema"],
        "globalSecondaryIndexes": [g["IndexName"] for g in desc.get("GlobalSecondaryIndexes", [])],
        "provisionedThroughput": {
            k: desc.get("ProvisionedThroughput", {}).get(k) for k in ("ReadCapacityUnits", "WriteCapacityUnits")
        },
        "itemCountAtStart": desc.get("ItemCount"),  # DynamoDB's own count, updated ~6-hourly; informational
        "rcuBudget": args.rcu,
        "format": "dynamodb-typed-json-lines-v1",
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    count = 0
    pages = 0
    print(f"export: {args.table} -> {args.out}  (budget {args.rcu} RCU/s)")
    with gzip.open(args.out, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({META_KEY: manifest}) + "\n")
        # Eventually consistent (the default) costs half the RCU of a consistent read and
        # is right for a backup: anything written in the last second is either in the
        # export or in the next one.
        paginator = ddb.get_paginator("scan")
        for page in paginator.paginate(
            TableName=args.table,
            ReturnConsumedCapacity="TOTAL",
            PaginationConfig={"PageSize": args.page_size},
        ):
            pages += 1
            for item in page["Items"]:
                fh.write(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n")
                count += 1
            pacer.account(page)
            if pages % 10 == 0:
                print(f"  {count} items, {pacer.consumed:.1f} RCU consumed")

    finished = datetime.now(timezone.utc)
    manifest.update(
        {
            "finishedAt": finished.isoformat(),
            "itemCount": count,
            "pages": pages,
            "rcuConsumed": round(pacer.consumed, 2),
            "seconds": round((finished - started).total_seconds(), 1),
        }
    )
    with open(args.out + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
        fh.write("\n")

    print(f"export: {count} items in {pages} pages, {pacer.consumed:.1f} RCU over {manifest['seconds']}s")
    print(f"export: manifest {args.out}.manifest.json")
    return 0


# ---------------------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------------------


def read_backup(path: str) -> tuple[dict, list[dict]]:
    items: list[dict] = []
    manifest: dict = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if META_KEY in obj:
                manifest = obj[META_KEY]
            else:
                items.append(obj)
    if manifest.get("format") != "dynamodb-typed-json-lines-v1":
        sys.exit(f"backup: {path} is not a backup this script wrote (no manifest line)")
    return manifest, items


def key_of(item: dict, key_schema: list[dict]) -> tuple:
    return tuple(json.dumps(item.get(k["AttributeName"]), sort_keys=True) for k in key_schema)


def do_restore(args: argparse.Namespace) -> int:
    ddb = client(args)
    manifest, items = read_backup(args.infile)
    desc = ddb.describe_table(TableName=args.table)["Table"]
    if desc["KeySchema"] != manifest["keySchema"]:
        sys.exit(f"backup: key schema mismatch: table {desc['KeySchema']} vs backup {manifest['keySchema']}")

    pacer = Pacer(args.wcu)
    print(f"restore: {len(items)} items from {args.infile} -> {args.table}  (budget {args.wcu} WCU/s)")
    started = time.time()
    written = 0
    retries = 0

    for start in range(0, len(items), BATCH):
        pending = [{"PutRequest": {"Item": it}} for it in items[start : start + BATCH]]
        backoff = 0.5
        while pending:
            resp = ddb.batch_write_item(RequestItems={args.table: pending}, ReturnConsumedCapacity="TOTAL")
            pacer.account(resp)
            unprocessed = resp.get("UnprocessedItems", {}).get(args.table, [])
            written += len(pending) - len(unprocessed)
            pending = unprocessed
            if pending:
                # UnprocessedItems is how BatchWriteItem reports throttling: not an error,
                # a partial result. Backing off here is what keeps the restore from
                # fighting the table at exactly the rate that produced the partial.
                retries += 1
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
        if (start // BATCH) % 20 == 0 and start:
            print(f"  {written}/{len(items)} written, {pacer.consumed:.0f} WCU consumed, {retries} partial batches")

    print(
        f"restore: {written} items in {time.time() - started:.1f}s, "
        f"{pacer.consumed:.0f} WCU consumed, {retries} partial batches retried"
    )
    return 0


# ---------------------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------------------


def do_verify(args: argparse.Namespace) -> int:
    ddb = client(args)
    manifest, items = read_backup(args.infile)
    key_schema = manifest["keySchema"]
    expected = {key_of(it, key_schema): it for it in items}
    pacer = Pacer(args.rcu)

    print(f"verify: {args.table} against {args.infile} ({len(expected)} items)")
    live: dict[tuple, dict] = {}
    for page in ddb.get_paginator("scan").paginate(
        TableName=args.table, ReturnConsumedCapacity="TOTAL", PaginationConfig={"PageSize": args.page_size}
    ):
        for item in page["Items"]:
            live[key_of(item, key_schema)] = item
        pacer.account(page)

    missing = [k for k in expected if k not in live]
    extra = [k for k in live if k not in expected]
    # Byte-equal after canonical JSON, so a number that came back as "1.0" for "1" or a
    # set in a different order is a difference this reports rather than hides.
    changed = [
        k
        for k in expected
        if k in live
        and json.dumps(expected[k], sort_keys=True, separators=(",", ":"))
        != json.dumps(live[k], sort_keys=True, separators=(",", ":"))
    ]

    print(f"  backup   {len(expected)} items")
    print(f"  table    {len(live)} items")
    print(f"  missing  {len(missing)}   (in backup, not in table)")
    print(f"  changed  {len(changed)}   (same key, different attributes)")
    print(f"  extra    {len(extra)}   (in table, not in backup{'; expected after new traffic' if extra else ''})")
    for k in (missing + changed)[:10]:
        print(f"    {k}")

    ok = not missing and not changed and (not extra or args.allow_extra)
    print(f"verify: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Shared flags, attached to every subcommand so they can go after it: `export --table`
    # reads naturally, and argparse would otherwise require them before the verb.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--table", default="linkpulse")
    common.add_argument("--region", default=None, help="defaults to AWS_REGION")
    common.add_argument("--endpoint-url", default=None, help="LocalStack, e.g. http://localstack:4566")
    common.add_argument("--page-size", type=int, default=100, help="items per Scan page (smaller = smoother pacing)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export", parents=[common], help="Scan the table into a gzipped JSON-lines file")
    p.add_argument("--out", default=None, help="default backups/<table>-<UTC timestamp>.jsonl.gz")
    p.add_argument("--rcu", type=float, default=10.0, help="read units per second to stay under (table has 25)")
    p.set_defaults(func=do_export)

    p = sub.add_parser("restore", parents=[common], help="BatchWriteItem every item in the file back into the table")
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--wcu", type=float, default=15.0, help="write units per second to stay under (table has 25)")
    p.set_defaults(func=do_restore)

    p = sub.add_parser("verify", parents=[common], help="Scan the table and compare with the file")
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--rcu", type=float, default=10.0)
    p.add_argument("--allow-extra", action="store_true", help="pass even if the table has items the backup lacks")
    p.set_defaults(func=do_verify)

    args = parser.parse_args()
    try:
        return args.func(args)
    except botocore.exceptions.ClientError as exc:
        err = exc.response.get("Error", {})
        print(f"backup: {err.get('Code')}: {err.get('Message')}", file=sys.stderr)
        return 2
    except botocore.exceptions.BotoCoreError as exc:
        print(f"backup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
