# LinkPulse — Case Study

A URL shortener with click analytics, built to be *operated* rather than merely to run:
the point was never the product but everything around it — a DynamoDB data model with
its failure mode designed in, infrastructure as code that applies against a mock in CI
and a real account on demand, a three-node Kubernetes cluster deployed by GitOps, a
monitoring stack whose alerts were proven by inducing the failures they describe, and
the automation to do all of that from a laptop with Docker and a task runner and nothing
else installed.

Two constraints shaped every decision: **$0** (an always-free AWS tier, with one
budgeted 72-hour burst of metered time), and **no host installs** — the whole thing has
to work on a machine where the operator cannot run `apt`, edit `/etc/hosts`, or import a
certificate. The second constraint turned out to be the more interesting one.

## What it is

A Go service with two paths. The redirect (`/r/{code}`) does one DynamoDB read, answers
302, and enqueues a click that eight background workers write later — so throttled
writes show up as lost analytics, never as slow redirects, and the alerting is built
around that fact. A GraphQL API creates links and reads their stats. One DynamoDB table,
provisioned at exactly the account's free allowance (20/20 on the table, 5/5 on the
index), with the click aggregate sharded across sixteen keys because a popular link is a
hot partition by construction.

Around it: Terraform in four environments (LocalStack for CI, a state bucket, the
always-free account, the metered burst — the last in its own state file so that
`destroy` is *incapable* of reaching the table); a k3d cluster pinned to the same minor
version as EKS; ArgoCD with an app-of-apps and two projects (the application's cannot
install a CRD; the platform's can); cert-manager issuing every ingress a certificate
from a CA the cluster owns; Sealed Secrets for the two values that are real on EKS;
Prometheus, Alertmanager, Grafana, Loki; and a set of scripts that back the table up,
audit what the account is spending, and run five chaos experiments that leave timelines
and rendered panels behind.

`docs/architecture.md` has the diagram; `tracker.md` has every decision with its
reasoning and every claim with the evidence that verified it.

## The numbers

Measured on k3d (three nodes, Docker Desktop for Windows) with LocalStack behind the
table. The burst re-measures on EKS and real DynamoDB; that comparison is the second
half of this document, not yet written.

**Baseline**, two minutes at 20 redirects/s and 2 GraphQL/s: redirect p50 12.9 ms,
p95 105 ms, p99 180 ms; zero failures; zero dropped iterations. The tail is DynamoDB
Local's — its `GetItem` p99 reaches a full second at 40/s while the nodes sit 80% idle —
so the thresholds are calibrated to it and the burst is expected to come in far under.

**Five experiments**, each codified and run to green, each ending with a 29-assertion
check that the monitoring stack is still observing:

| # | Failure | What happened, timed |
|---|---|---|
| 1 | DynamoDB refuses half of all click writes while 20 redirects/s hit one link | first drop 10 s in; alert 65 s in; **redirect p99 25 ms vs 21 ms clean; zero 5xx**; half the clicks lost and counted |
| 2 | A deploy whose pods start but never serve, shipped through GitOps | old pods kept serving; `ProgressDeadlineExceeded` at 204 s; alert and ArgoCD Degraded at 285 s; `git revert` rolled it back in 109 s, nobody touched the cluster |
| 3 | The datastore vanishes | process not-ready 17 s; both alerts at 92 s, the per-pod one inhibited; **0 restarts**; 5 s to recover |
| 4 | A pod leaks memory past its limit, five times | warning at 94 s; OOMKilled at 107 s, alert at 127 s; CrashLoopBackOff alert on the fifth kill; the other replica served throughout |
| 5 | A node is drained, then stopped | drain: one eviction allowed by the PDB, replacement ready in 7 s; stop: NotReady at 49 s, alerts at ~135 s, **285 s on one replica** until the taint eviction; redirects served throughout |

## What was found

The project's value is less in what it demonstrates than in what building it turned up.
In order of how much each would have cost:

**The always-free environment was not free.** The table was provisioned at 25/25 and the
index at 5/5, and the module validated the table alone against the 25-unit allowance —
which is per account and includes indexes. Thirty units per side is roughly $3 a month on
an environment whose one defining property is $0. Found by the new `metered-resources.py`
on its first run against LocalStack, in phase 8, before any real account existed. The
table is 20/20 now and the module validates the sum.

**Two alert rules could never fire.** `LinkpulseMemoryNearLimit` and
`LinkpulseBelowMinReplicas` joined series from different exporters with a bare operator;
PromQL matches on the full label set, so both were permanently empty — loaded, healthy,
and silent. They passed the observability check for two phases and one cluster rebuild.
Experiment 4's harness found the first by querying the expression before injecting
anything; experiment 5's first run found the second by holding the deployment at one
replica for five minutes with no alert. `docs/postmortem.md` is the write-up.

**LocalStack does not enforce provisioned capacity**, and a compose comment said it did.
Measured: 78 write units per second against a 20-unit table, zero throttles. Experiment 1
now injects `ProvisionedThroughputExceededException` through LocalStack's runtime config
API — the exception, the retry, the drop, the alert are all real; only DynamoDB's reason
is not — and the burst runs the same traffic un-injected.

**The sharding mitigation cannot be demonstrated on a 20-WCU table.** A table this small
has one partition; the per-partition ceiling (1000 WCU) is fifty times the table's, so
throttling at 20 is the table's bucket, not a hot key's, and sixteen shards versus one
should throttle identically on the burst. The design is right at scale and the evidence
for it is the write distribution (verified: sixty clicks over sixteen keys). This is
recorded as the honest expectation for the burst rather than discovered on the clock.

**A provider/LocalStack incompatibility, bisected rather than worked around.** AWS
provider 6.13+ cannot see a DynamoDB table it just created on LocalStack ≤ 4.9. The
tempting fix was a provider pin; the right one was a LocalStack floor, found by testing
both directions. The pin would have broken real AWS while looking fine locally.

**The IRSA trust policy named a service account that does not exist.** `linkpulse-api`
in Terraform, `linkpulse` in the manifests. On EKS this is `AccessDenied` inside the pod
with nothing wrong on either side individually. Found while writing the burst runbook,
which is what runbooks written before the clock starts are for.

**Draining a node takes the observers with it.** Traefik, CoreDNS, Prometheus,
Alertmanager, kube-state-metrics and Grafana are single replicas with no disruption
budgets; the first attempt at experiment 5 evicted the harness's own eyes. The experiment
now moves them off the target first and says why a production cluster would not need to.

**Smaller, and all recorded in the tracker:** `git revert -q` is not a flag (made twice,
caught both times by an assertion on the cluster rather than on the exit code); a
Prometheus `CounterVec` exports nothing until its first observation, so a rate over it
returns *no data* rather than zero and the experiment-1 alert would have been blind;
`kubectl wait --for=create` with two resource names fails instead of waiting; the
kube-state-metrics liveness probe pointed at a path that returns 404 and the kubelet
killed a healthy exporter every 45 seconds; Docker Desktop's VM clock can jump half an
hour mid-experiment.

## What the constraints bought

The **no-host-installs** rule was expected to be a nuisance and turned out to be a design
pressure. Every task is a single tool invocation inside a pinned image, because the k3d
image has no shell and Windows has no `sh`; that made the task file readable by anyone
and identical on three operating systems. TLS came from an in-cluster CA instead of
mkcert, because `mkcert -install` mutates the host — and the in-cluster version renews
itself and is declared in git. The ingress uses `localtest.me` because a hosts-file edit
needs administrator rights. None of these were the first idea; all of them are better
than the first idea.

The **$0** rule made cost a property to be measured rather than a bill to be read. The
metered-resource audit exists because `terraform destroy` cannot see an ALB the load
balancer controller created; the backup script paces itself under the table's read
allowance because an unpaced `Scan` would throttle the application it protects; the
burst environment is a separate state file because the most dangerous command in the
project has to be safe to run in a hurry.

## What is not done, and what is next

Nothing has touched a real AWS account: there were no credentials on the machine this
was built on. Everything that targets AWS — the always-free and burst environments, the
load-balancer controller, the cost report, the teardown that waits for the ALB — is
validated and unexecuted, and `docs/burst-runbook.md` is the plan for the first
execution. CI has never run, for the same reason: the repository has no remote yet. The
tracker's "known loose ends" is the complete list, kept current since phase 0.

What I would do differently: write the experiments before the alert rules, not after.
The rules were the deliverable and the experiments the demonstration; it should have
been the other way round, and for the two rules that could never fire, it eventually was.

## Reading order

1. `README.md` — how to run it (fifteen minutes cold, one warm).
2. `docs/architecture.md` — the diagram and the trade-offs.
3. `docs/data-model.md` — the design that everything else serves.
4. `docs/evidence/chaos-*/timeline.md` — what the failures actually looked like.
5. `docs/postmortem.md` — the incident the project had with itself.
6. `docs/runbook.md` — what to do when each alert fires.
7. `tracker.md` — every decision, every bug, every verification, in order.
