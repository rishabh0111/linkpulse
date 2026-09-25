# Burst evidence: EKS and real DynamoDB, 2026-09-24

One EKS cluster (1.34, 4 × t3.small, ap-south-1) against the real `linkpulse` table
(20 WCU / 20 RCU, GSI1 5/5), up from 07:37 to about 19:55 local (IST, UTC+5:30). Times below are
local unless marked UTC. Panel images are rendered in UTC.

Every run is here, including the ones that failed. A failed run is recorded as failed,
with the reason, rather than rerun until it passed.

| Run | Result | Files |
|---|---|---|
| Smoke through the ALB | PASS: A1–A7 against real DynamoDB, via IRSA | (console output; see the tracker) |
| Observability check | PASS 29/29, after three fixes (tracker, Phase 10) | (console output) |
| k6 baseline | PASS: 2,667 requests, 0 failed, redirect p95 83 ms, p99 155 ms | `k6-baseline.json` |
| Chaos 1, throttling | PASS (second run) | `chaos-1/` |
| Chaos 5, node loss | FAIL in the drain half, by design of the capacity, not the app | `chaos-5/run.json`, `chaos-5/run.log` |
| Chaos 3, datastore unreachable | FAIL: IAM propagation is not simultaneous | `chaos-3/run.json`, `chaos-3/run.log` |
| Chaos 2, 4 | not run on EKS, by decision | see below |
| Cost | $3.32 for the whole burst | `cost-report-final.md` |

## Chaos 1: real throttling, nothing injected

`CHAOS_INJECT=none`. 20 redirects/s on one link, which is 40 WCU/s against a 20 WCU table.

- The table first spent its burst credit (up to 300 s of unused capacity). Throttling
  began 209 s after the clean window, about 5 minutes into the load, as predicted.
- `LinkpulseClicksDropped{reason="throttled"}` and `LinkpulseDynamoThrottled` fired 30 s
  after the first drop.
- DynamoDB threw 6.6 throttles/s at the app. Users saw none of it: 7,202 requests,
  0 failed, every redirect a 302, redirect p99 7 ms against 5 ms before.
- `cloudwatch-throttling.png` is DynamoDB's own view, from the CloudWatch exporter:
  consumed WCU at 40/s while burst credit lasts, then clamped to exactly the provisioned
  20 while write throttle events rise to about 20/s. Both runs of the experiment are in
  it (the first, 13:15–13:36 UTC, and the recorded one, 13:39–13:46 UTC). The tail
  after each run is the exporter repeating its last datapoint for up to its 300 s
  look-back.
- CloudWatch's `ThrottledRequests` by operation: PutItem 1,304/min against UpdateItem
  14/min at peak. Almost all throttled writes were the per-click event records, not the
  hot aggregate counter that `docs/data-model.md` worries about.

The first run of this experiment (not kept) failed one check: its load ran 12 minutes
instead of the task's 6, so it was still running when `during` waited for drops to
stop. That was the operator's parameter, not the system. The kept run uses the task's
6 minutes and passes every check.

## Chaos 5: drain, stopped at the first half

Target `ip-10-42-15-154`, one app replica. The drain evicted the replica through the
PDB in 7 s, and its replacement never scheduled: `0/4 nodes are available: 1 node(s)
were unschedulable, 3 Too many pods`. The other three nodes were at the VPC CNI's
11-pod cap. The spare slots were all on the node being drained, partly because the
experiment's own preparation moved four single-replica pods onto the others.

The app ran on one replica for about 4 minutes (uncordoned by hand at 19:05) with 0 5xx
in about 3,800 requests. The PDB limited how many pods left. It cannot create room for
them to land. N+1 on EKS has to be counted in pod slots.

The loss half (stopping the EC2 instance) was not run: with the same capacity its
replacement pod would have been Pending until the node returned, restating this result
while leaving the app on one replica again. The node group's auto-repair was never
suspended, because the run stopped before that step.

## Chaos 3: the datastore goes away, via IAM

The EKS injection the script's docstring names: detach `linkpulse-burst-table-access`
from the IRSA role, then re-attach. Measured from Prometheus before the cluster was
destroyed:

| | |
|---|---|
| Policy detached | 19:24:28 |
| Pod `…4x4nr` NotReady (AccessDenied on DescribeTable) | 19:25:47 (+80 s) |
| Pod `…q5gz9` | never NotReady |
| Policy re-attached | 19:26:37 |
| Pod `…4x4nr` Ready again | 19:30:47 (+4 min 10 s after the re-attach) |

The experiment asserts that every pod goes unready together. That holds for a paused
LocalStack and not for an IAM change: propagation is eventually consistent, and here it
reached one pod's session and not the other's within two minutes, then took four
minutes to undo. The application's readiness cache is 2 s and played no part. For a
runbook: after reverting a bad IAM change, expect minutes more of AccessDenied.

## Not run on EKS, by decision

- Chaos 4 (memory exhaustion) needs `POST /debug/leak`, an endpoint that lets any caller
  make the process exhaust its memory. Only the local overlay enables it, by design: a
  deployment facing the internet should not have it, and running the experiment on EKS
  would have meant shipping exactly that.
- Chaos 2 (bad deploy and rollback) drives the local Gitea remote. On EKS ArgoCD syncs
  from GitHub, so it would have meant pushing a deliberately broken image to the public
  registry and a bad commit to the public `main`, to prove what the local run already
  proves. What that leaves unexercised is narrow: ArgoCD's sync from GitHub ran on EKS
  all day (every fix in this burst reached the cluster that way); only the
  broken-deploy-then-revert sequence did not.

Both are covered by the local evidence under `docs/evidence/chaos-2` and `chaos-4`.
