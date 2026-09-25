# LinkPulse

[![CI](https://github.com/rishabh0111/linkpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/rishabh0111/linkpulse/actions/workflows/ci.yml)
![Go](https://img.shields.io/badge/Go-00ADD8?style=flat&logo=go&logoColor=white)
![DynamoDB](https://img.shields.io/badge/DynamoDB-4053D6?style=flat&logo=amazondynamodb&logoColor=white)
![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?style=flat&logo=kubernetes&logoColor=white)
![Amazon EKS](https://img.shields.io/badge/Amazon_EKS-FF9900?style=flat&logo=amazoneks&logoColor=white)
![Argo CD](https://img.shields.io/badge/Argo_CD-EF7B4D?style=flat&logo=argo&logoColor=white)
![Terraform](https://img.shields.io/badge/Terraform-844FBA?style=flat&logo=terraform&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-F46800?style=flat&logo=grafana&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)

**[Full write-up](https://rishabh0111.github.io/blogs/production-grade-devops-platform/)** ·
[Chaos evidence](docs/evidence/) ·
[The EKS run](docs/evidence/burst/README.md) ·
[Data model](docs/data-model.md) ·
[Runbook](docs/runbook.md) ·
[CI runs](https://github.com/rishabh0111/linkpulse/actions/workflows/ci.yml)

A URL shortener with click analytics, and the platform around it. A Go service over a
single DynamoDB table, deployed to Kubernetes by ArgoCD, with Terraform for four
environments, a Prometheus and Loki monitoring stack, and five chaos experiments that
induce the failures its alerts claim to catch. It runs on k3d with LocalStack locally and
in CI, and has run on EKS against real DynamoDB.

## Running it

```sh
sh scripts/bootstrap.sh       # Windows: powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
mise run dev
```

Needs Docker (Desktop or Engine, Compose v2) and [mise](https://mise.jdx.dev), which the
bootstrap script installs into your home directory. Everything else, Go, Terraform,
kubectl, k6 and Python, runs in pinned containers. Nothing system-wide changes: no hosts
file, no trust store, no sudo. On SELinux hosts the socket-mounting services run
unlabelled and the repo mounts use `:z`.

`dev` starts LocalStack, applies the Terraform, creates a three-node k3d cluster, installs
cert-manager and Sealed Secrets, deploys the application and monitoring, then checks it:
the smoke test over verified TLS, the 29-assertion observability check, the TLS check on
every hostname and a Sealed Secrets round trip. About fifteen minutes cold, one warm.

| | |
| --- | --- |
| App | http://linkpulse.localtest.me:8088, https://linkpulse.localtest.me:8443 |
| Grafana | http://grafana.localtest.me:8088 (anonymous viewer; `admin`/`admin` to edit) |
| Prometheus | http://prometheus.localtest.me:8088 |
| Alertmanager | http://alertmanager.localtest.me:8088 |

`localtest.me` resolves to `127.0.0.1` in public DNS. `mise run tls-ca` exports the
cluster's CA to `certs/ca.crt` if a browser should trust the `https://` names.

## Try it

```sh
API=http://linkpulse.localtest.me:8088

# Shorten a URL
curl -s $API/graphql -H 'Content-Type: application/json' \
  -d '{"query":"mutation { shortenUrl(longUrl: \"https://example.com/a/long/path\") { code shortUrl } }"}'
# -> {"data":{"shortenUrl":{"code":"<CODE>","shortUrl":".../r/<CODE>"}}}

# Follow it: one read, then 302. The click is recorded afterwards, off the request path.
curl -si $API/r/<CODE> | head -3

# Read the analytics
curl -s $API/graphql -H 'Content-Type: application/json' \
  -d '{"query":"{ link(code: \"<CODE>\") { totalClicks clicksByDay { day count } recentClicks(limit: 5) { at country uaClass } } }"}'
```

`shortenUrl` refuses `javascript:` and other non-HTTP schemes with a `400`. An unknown or
malformed code answers `404`.

## API

| | |
| --- | --- |
| `GET /r/{code}` | Redirect: one DynamoDB read, `302`, click enqueued for eight background writers |
| `POST /graphql` | `shortenUrl`, `link(code)`, `links(ownerId)` with `totalClicks`, `clicksByDay`, `recentClicks` |
| `GET /graphql` | Queries only; mutations over `GET` are refused |
| `GET /healthz` | Liveness: the process, never the datastore |
| `GET /readyz` | Readiness: a `DescribeTable`, cached 2 s |
| `GET /metrics` | Prometheus |
| `GET /` | The dashboard |

## The claims, and the evidence behind each

| Claim | Evidence |
| --- | --- |
| A redirect never waits for its click to be written | [`TestRedirectDoesNotBlockOnClickWrite`](app/graphql-api/internal/httpapi/router_test.go) |
| When the click queue is full, clicks are shed and counted, and redirects carry on | [`TestClickQueueShedsWhenFull`](app/graphql-api/internal/httpapi/router_test.go) |
| DynamoDB throttling costs analytics, never redirects: zero 5xx, redirect p99 unchanged | Experiment 1: [locally, injected](docs/evidence/chaos-1/timeline.md); [on real DynamoDB, not injected](docs/evidence/burst/chaos-1/timeline.md), with [CloudWatch's view](docs/evidence/burst/chaos-1/cloudwatch-throttling.png) |
| Losing the datastore makes pods unready and never restarts them | [`TestLivenessIgnoresStoreOutage`](app/graphql-api/internal/httpapi/router_test.go), [`TestReadinessFailsOnStoreOutage`](app/graphql-api/internal/httpapi/router_test.go), experiment 3 ([timeline](docs/evidence/chaos-3/timeline.md)) |
| A deploy that never becomes ready never replaces the pods serving traffic, and `git revert` rolls it back | Experiment 2 ([timeline](docs/evidence/chaos-2/timeline.md)), through ArgoCD with nobody touching the cluster |
| A memory leak is OOM-killed, alerted on, and caught as CrashLoopBackOff while the other replica serves | Experiment 4 ([timeline](docs/evidence/chaos-4/timeline.md)) |
| A drain respects the disruption budget; a lost node is reported and its pod replaced | Experiment 5 ([timeline](docs/evidence/chaos-5/timeline.md)) |
| Every alert rule can fire, and fires on the failure it names | The five experiments assert each rule by inducing its condition; rules in [`monitoring/prometheus/rules/`](monitoring/prometheus/rules/) |
| The monitoring stack is observing: targets up, rules evaluating, logs and dashboards queryable | [`scripts/observability-check.py`](scripts/observability-check.py), 29 assertions |
| The click aggregate is sharded, and a day's range read covers every shard | [`TestWriteShardingSpreadsAggregateKeys`](app/graphql-api/internal/store/keys_test.go), [`TestStatRangeCoversAllShards`](app/graphql-api/internal/store/keys_test.go) |
| Dangerous URLs are refused | [`TestShortenRejectsDangerousURLs`](app/graphql-api/internal/gql/schema_test.go) |
| The always-free environment has nothing billing by the hour, and its capacity cannot exceed the free allowance | [`scripts/metered-resources.py`](scripts/metered-resources.py) `--expect-none`; the table module validates the table and index sum to 25 per side |
| A backup restores the table byte for byte after it is destroyed | [`scripts/backup.py`](scripts/backup.py) `verify`, in CI after `terraform destroy` and re-apply |
| Every ingress serves a certificate from the cluster's own CA | [`scripts/tls-check.py`](scripts/tls-check.py) |
| A value sealed offline decrypts only in the cluster holding the key | [`scripts/seal-check.sh`](scripts/seal-check.sh) |
| The same manifests run on EKS against real DynamoDB, with IRSA and no stored credential | [The EKS run](docs/evidence/burst/README.md): smoke, observability 29/29, k6 baseline, experiment 1 |

CI runs the unit tests, every static check, and on a three-node cluster the smoke,
observability, TLS and Sealed Secrets checks, the k6 baseline, experiments 1, 3, 4 and 5,
and the backup restore, on every push. Experiment 2 needs the GitOps path:
`mise run gitops && mise run chaos-2`.

## Development

```sh
mise run app-test             # gofmt, vet, unit tests
mise run tf-check             # terraform fmt and validate, every environment
mise run k8s-render           # render every kustomization
mise run gitops               # Gitea as the remote, ArgoCD deploying from it
mise run k6-baseline          # two minutes of load; thresholds are the verdict
mise run chaos-3              # or chaos-1 .. chaos-5, or chaos-all after gitops
mise run clean                # remove the cluster and all local state
mise tasks                    # everything, with descriptions
```

`make <task>` forwards to `mise run <task>`. Every task is one tool invocation in a
container, so they behave the same on Linux, macOS and Windows.

## Configuration

The service reads its environment. Defaults are in
[`internal/config/config.go`](app/graphql-api/internal/config/config.go).

| Variable | Default | |
| --- | --- | --- |
| `DDB_TABLE` | `linkpulse` | Table name |
| `DDB_ENDPOINT` | empty | Empty is real AWS; LocalStack locally |
| `AWS_REGION` | `ap-south-1` | |
| `PUBLIC_BASE_URL` | | Base of `shortUrl` |
| `CLICK_SHARDS` | `16` | Shards per day for the click aggregate |
| `CLICK_WORKERS` | `8` | Background click writers |
| `CLICK_QUEUE_SIZE` | `2048` | Clicks buffered before shedding |
| `CLICK_TTL_DAYS` | `30` | Click records expire after this |
| `READY_CACHE_TTL` | `2s` | How long a readiness result is reused |
| `SHUTDOWN_GRACE` | `15s` | Drain time on `SIGTERM` |
| `PORT` | `8080` | |

`LINKPULSE_ENABLE_DEBUG_LEAK` and `LINKPULSE_BREAK_READINESS` exist for experiments 4
and 2. Only the local overlay and the deliberately broken image set them.

## Deploying to AWS

Terraform environments under [`infra/terraform/envs/`](infra/terraform/envs/): `bootstrap`
(the state bucket), `aws` (always free: the table, IAM, a VPC with no NAT, budgets) and
`aws-burst` (EKS, in its own state so `destroy` cannot reach the table).
[`docs/burst-runbook.md`](docs/burst-runbook.md) is the procedure, including the teardown
that waits for the load balancer to be released before destroying the cluster.

## Further reading

Why it is built this way, and what running it on AWS found:
[the full write-up](https://rishabh0111.github.io/blogs/production-grade-devops-platform/).

The design the rest serves: [`docs/data-model.md`](docs/data-model.md). The diagram:
[`docs/architecture.md`](docs/architecture.md). What to do when each alert fires:
[`docs/runbook.md`](docs/runbook.md). An incident with its own alert rules:
[`docs/postmortem.md`](docs/postmortem.md). Every decision and verification, in order:
[`tracker.md`](tracker.md).

The vendored manifests under `*/upstream/` (ArgoCD, cert-manager, Sealed Secrets,
metrics-server) are unmodified copies of their releases, under their own Apache-2.0
licences.
