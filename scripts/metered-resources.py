#!/usr/bin/env python3
"""List everything in the account that bills by the hour. The teardown gate.

`terraform destroy` on envs/aws-burst removes what that state file knows about. It does
not know about the ALB the load balancer controller created from an Ingress, the
security groups that controller attached to it, an EBS volume left by a PersistentVolume,
or an Elastic IP something allocated and forgot -- and every one of those bills whether
or not the cluster it belonged to still exists. The $1 budget tripwire would catch them
in a day or two. This catches them in the same minute as the destroy, which during a
metered window is the difference that matters.

It asks each service directly for the things that cost money by existing:

    eks        clusters                          $0.10/hr each
    ec2        instances (not terminated)        the node group, or anything else
    ec2        NAT gateways (not deleted)        ~$0.045/hr, the classic leak
    ec2        Elastic IPs                       billed whether attached or not
    ec2        EBS volumes                       per GB-month, including unattached
    elbv2      load balancers (ALB/NLB)          ~$0.0225/hr, the controller's, by Ingress
    elb        classic load balancers            from a Service type=LoadBalancer without the controller
    dynamodb   tables above the free 25/25       the one always-free resource, checked for drift
    dynamodb   point-in-time recovery enabled    per GB-month; off by design

With --expect-none it exits 1 if any of the hourly items exist, which is what makes it a
gate rather than a report: `mise run burst-down` runs it after the destroy and a non-zero
exit is a leak to go and find. Without the flag it is the "what is running right now"
answer, useful on any morning of the window.

Runs in the ops container with the operator's read-only credentials. Every probe is
independent and reports its own failure, so an API that is not reachable (no permission,
or LocalStack, which has none of eks/elbv2) shows up as one line and does not hide the
others -- `mise run backup-check` runs it against LocalStack for exactly that reason.

    python3 scripts/metered-resources.py
    python3 scripts/metered-resources.py --expect-none
    python3 scripts/metered-resources.py --endpoint-url http://localstack:4566   # dynamodb only, the rest report unreachable
"""

from __future__ import annotations

import argparse
import os
import sys

import botocore.config
import botocore.exceptions
import botocore.session

FREE_CAPACITY = 25


def tag(tags: list | None, key: str = "Name") -> str:
    for t in tags or []:
        if t.get("Key") == key:
            return t.get("Value", "")
    return ""


class Report:
    def __init__(self) -> None:
        self.metered: list[str] = []      # lines that mean money is being spent
        self.unreachable: list[str] = []  # probes that could not run
        self.notes: list[str] = []        # informational

    def probe(self, label: str, fn):
        try:
            found = fn()
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
            self.unreachable.append(f"{label}: {code}")
            print(f"  ?     {label}: {code}")
            return
        if found:
            for line in found:
                self.metered.append(f"{label}: {line}")
                print(f"  $$    {label}: {line}")
        else:
            print(f"  none  {label}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=None, help="defaults to AWS_REGION")
    parser.add_argument("--endpoint-url", default=None, help="LocalStack")
    parser.add_argument("--expect-none", action="store_true", help="exit 1 if anything hourly exists")
    parser.add_argument("--table", default="linkpulse")
    args = parser.parse_args()

    region = args.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        sys.exit("metered-resources: no region: pass --region or set AWS_REGION")

    session = botocore.session.get_session()
    cfg = botocore.config.Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30)

    def c(service: str):
        return session.create_client(service, region_name=region, endpoint_url=args.endpoint_url, config=cfg)

    print(f"metered-resources: {region}" + (f" via {args.endpoint_url}" if args.endpoint_url else ""))
    r = Report()

    # ---- hourly ---------------------------------------------------------------------
    def eks():
        return [f"cluster {n}" for n in c("eks").list_clusters()["clusters"]]

    def instances():
        out = []
        for page in c("ec2").get_paginator("describe_instances").paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
        ):
            for res in page["Reservations"]:
                for i in res["Instances"]:
                    out.append(f"{i['InstanceId']} {i['InstanceType']} {i['State']['Name']} {tag(i.get('Tags'))}")
        return out

    def nat():
        return [
            f"{n['NatGatewayId']} {n['State']} {tag(n.get('Tags'))}"
            for n in c("ec2").describe_nat_gateways(Filter=[{"Name": "state", "Values": ["pending", "available", "deleting"]}])["NatGateways"]
        ]

    def eips():
        return [
            f"{a.get('PublicIp')} {'attached to ' + a['AssociationId'] if a.get('AssociationId') else 'UNATTACHED'} {tag(a.get('Tags'))}"
            for a in c("ec2").describe_addresses()["Addresses"]
        ]

    def volumes():
        out = []
        for page in c("ec2").get_paginator("describe_volumes").paginate():
            for v in page["Volumes"]:
                out.append(f"{v['VolumeId']} {v['Size']}GiB {v['State']} {tag(v.get('Tags'))}")
        return out

    def albs():
        out = []
        for page in c("elbv2").get_paginator("describe_load_balancers").paginate():
            for lb in page["LoadBalancers"]:
                out.append(f"{lb['LoadBalancerName']} {lb['Type']} {lb['Scheme']}")
        return out

    def classic_elbs():
        out = []
        for page in c("elb").get_paginator("describe_load_balancers").paginate():
            for lb in page["LoadBalancerDescriptions"]:
                out.append(f"{lb['LoadBalancerName']} classic")
        return out

    print("hourly")
    r.probe("eks clusters", eks)
    r.probe("ec2 instances", instances)
    r.probe("nat gateways", nat)
    r.probe("elastic ips", eips)
    r.probe("ebs volumes", volumes)
    r.probe("load balancers (elbv2)", albs)
    r.probe("load balancers (classic)", classic_elbs)

    # ---- the table: free only while it stays at or under 25/25 with PITR off ----------
    def table():
        out = []
        ddb = c("dynamodb")
        names = []
        for page in ddb.get_paginator("list_tables").paginate():
            names += page["TableNames"]
        r.notes.append(f"dynamodb tables: {', '.join(names) or 'none'}")
        for name in names:
            t = ddb.describe_table(TableName=name)["Table"]
            pt = t.get("ProvisionedThroughput", {})
            rcu = pt.get("ReadCapacityUnits", 0)
            wcu = pt.get("WriteCapacityUnits", 0)
            for g in t.get("GlobalSecondaryIndexes", []):
                gpt = g.get("ProvisionedThroughput", {})
                rcu += gpt.get("ReadCapacityUnits", 0)
                wcu += gpt.get("WriteCapacityUnits", 0)
            mode = t.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
            if mode != "PROVISIONED":
                out.append(f"{name}: billing mode {mode} has no free tier")
            elif rcu > FREE_CAPACITY or wcu > FREE_CAPACITY:
                out.append(f"{name}: {rcu} RCU / {wcu} WCU including indexes, above the free {FREE_CAPACITY}")
            else:
                r.notes.append(f"{name}: {rcu} RCU / {wcu} WCU including indexes (free)")
            try:
                pitr = ddb.describe_continuous_backups(TableName=name)["ContinuousBackupsDescription"]
                if pitr.get("PointInTimeRecoveryDescription", {}).get("PointInTimeRecoveryStatus") == "ENABLED":
                    out.append(f"{name}: point-in-time recovery ENABLED (billed per GB-month)")
            except botocore.exceptions.ClientError as exc:
                r.notes.append(f"{name}: PITR status unavailable ({exc.response['Error']['Code']})")
        return out

    print("monthly / free-tier drift")
    r.probe("dynamodb", table)

    print()
    for n in r.notes:
        print(f"  note  {n}")
    if r.unreachable:
        print(f"  {len(r.unreachable)} probe(s) could not run: " + "; ".join(r.unreachable))

    if r.metered:
        print(f"\nmetered-resources: {len(r.metered)} billing item(s) present")
        return 1 if args.expect_none else 0
    if r.unreachable and args.expect_none:
        # "Nothing found" is not "nothing there" when a probe did not run. A gate that
        # passes because it could not look is worse than one that fails loudly.
        print("\nmetered-resources: nothing found, but not every probe ran -- cannot certify none")
        return 1
    print("\nmetered-resources: nothing billing by the hour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
