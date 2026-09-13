# LinkPulse — Implementation Tracker

Running record of what has been built, what was decided and why, and what is verified
versus merely written. Updated at the end of each phase.

Plan of record: `../plan/linkpulse-plan.md`. Phase numbering follows the plan's §7 Order
of Work.

**Status:** Phases 0–9 and 11 complete. Phase 10 (the AWS burst) is the only remaining phase and is blocked on an AWS account; `docs/burst-runbook.md` is ready for it.

The repository is on GitHub, and **CI has passed end to end** — run 7, all eight jobs, a 53-minute e2e job through the load baseline, chaos 3, 1, 4 and 5, and the destroy-and-restore backup. It took seven runs. Five defects were found on the way, each hidden behind the one before, and three were the same kind: a step reporting success after failing (k6's summary export, `k3d image import`, and this repository's own `phase || recover` chaos steps). Run 8, carrying the last of those fixes, passed too, and its log was checked the same way: no hidden failures, and each chaos recovery ran exactly once.

---

## Phase 0 — Environment survey ✅

Checked what the two host prerequisites actually give us before writing anything.

| Tool | Result |
|---|---|
| Docker | 29.7.2, Docker Desktop, linux/x86_64 containers |
| git | 2.55.0 |
| Node | v24.19.0 (host, not required by the project) |
| Python | 3.11.9 (host) |
| mise, go, terraform, helm, k3d, **make**, jq | **absent** |
| Network | `proxy.golang.org` 200, `ghcr.io` reachable |

The absence of a host toolchain is the point, not a problem: it is the condition §4.9
claims to work under. Everything below was built and verified **inside containers**, with
no host installs.

Repo initialised at `linkpulse/` with the layout from plan §5, `git init` done.

---

## Phase 1 — Data model, written first ✅

**`docs/data-model.md`** — written before any application code, as the plan requires.

- Access patterns A1–A7 enumerated up front, each with frequency and consistency needs.
- Four item types (LINK, CLICK, CLICKSTAT) with PK/SK rationale per pattern.
- **Hot-partition section as the centrepiece**: why the 1000 WCU per-partition limit means
  raising table capacity cannot fix it, why `BatchWriteItem` cannot either, write-sharding
  as the mitigation, the read-amplification trade-off it buys, and DynamoDB Streams named
  as the rejected alternative with the condition that would make me pick it.
- Capacity budget: 2 WCU per click against 25 WCU → **~12 clicks/sec ceiling**, the number
  chaos experiment 1 exists to cross.
- "When single-table is the wrong choice", since the plan says I will be asked.

Revised during Phase 2: the GSI1 projection tightened from `INCLUDE (code, longUrl)` to
`INCLUDE (longUrl, active)` — `code` and `createdAt` are recoverable from key attributes an
index always projects, so projecting them was paying index-write bytes for nothing.

---

## Phase 2 — Application ✅ (verified: gofmt + vet clean, all tests pass)

Go 1.27, module `linkpulse`, at `app/graphql-api/`.

| File | What it holds |
|---|---|
| `cmd/server/main.go` | wiring, JSON logging, SIGTERM handling, ordered graceful shutdown |
| `internal/config/config.go` | all config from env, so one image serves LocalStack / AWS / k3d / EKS |
| `internal/store/model.go` | `Store` interface, one method per access pattern A1–A7 |
| `internal/store/keys.go` | the key construction from the data-model doc |
| `internal/store/dynamo.go` | DynamoDB implementation, endpoint-switchable, fully instrumented |
| `internal/store/memory.go` | in-process store so CI unit tests need no container |
| `internal/gql/schema.go` | programmatic GraphQL schema, URL validation, collision retry |
| `internal/httpapi/router.go` | GraphQL endpoint, redirect hot path, RED metrics middleware |
| `internal/httpapi/clicks.go` | asynchronous click recorder, bounded queue, drain on shutdown |
| `internal/httpapi/health.go` | liveness / readiness split, plus experiment-4 fault injection |
| `internal/httpapi/static/index.html` | minimal dashboard, embedded in the binary, polls the feed |
| `internal/metrics/metrics.go` | every exported series, in one place, treated as an interface |

### Decisions closed in this phase

**Go for the API.** Static binary → distroless image with no OS packages, which is what
makes the Trivy HIGH/CRITICAL gate passable rather than aspirational; also the language of
the Kubernetes ecosystem I am claiming competence in. Node would iterate faster locally but
ships more CVE surface — a liability given the gate.

**`graphql-go/graphql`, schema built in code.** gqlgen would add a `go generate` step CI
must run *and* verify is not stale. The schema is small enough that the generator costs
more than it returns.

**`CLICK_SHARDS` is configuration, not code.** The highest-leverage decision in the phase.
Experiment 1 reproduces the throttle at `CLICK_SHARDS=1` and demonstrates the mitigation at
`CLICK_SHARDS=16` **running the identical binary under identical load** — a config flip,
not a rewrite that changes two variables at once. Published as `linkpulse_click_shards` so
a dashboard can annotate which side of the flip a run is on. **Both sides are now
demonstrated on real infrastructure — see Phase 4 evidence.**

**Clicks are written asynchronously, and that shapes the alerting.** A click costs 2 WCU
against a 25 WCU table, so the aggregate write throttles first. If the redirect awaited it,
every user would absorb DynamoDB retry latency and the service would fail closed on an
analytics problem. Because it does not, **saturation appears as dropped clicks, not slow
redirects** — so an alert watching only latency would miss the incident. Hence
`linkpulse_clicks_dropped_total{reason}`, which the experiment-1 assertion reads.

**Liveness does not check DynamoDB; readiness does.** Otherwise experiment 3 (revoke the
IAM policy) would restart every pod in a loop and turn a dependency outage into a
self-inflicted CrashLoopBackOff. Pinned by a test.

**The bad deploy is a real image, not a runtime flag.** `LINKPULSE_BREAK_READINESS` is
baked by a `--build-arg`, so experiment 2's artifact is a distinct image with its own
digest. It fails readiness while liveness passes — which is why the rollout *stalls*
instead of crash-looping, the signature of the most common real bad deploy. Pinned by a
test.

**Experiment 4's exhaustion is injected, and says so.** Load alone does not reliably push a
Go service past a memory limit (the GC keeps steady state flat), and an experiment that
only sometimes triggers cannot carry a CI assertion. A guarded `/debug/leak` endpoint (off
unless `LINKPULSE_ENABLE_DEBUG_LEAK=true`, touching every page so they count against the
cgroup limit) makes it deterministic. The OOMKill, restart, CrashLoopBackOff and alert are
all genuine; only the trigger is injected, and the write-up will say so.

**`302` not `301`.** A permanent redirect gets cached by the browser, and a cached redirect
records no click.

### Three real bugs, found by testing

All three would have quietly broken the evidence pipeline the portfolio rests on:

1. **Validation errors returned HTTP 500.** `graphql.Do()` returns parse, validation and
   resolver failures in one undifferentiated slice. Fixed by splitting it into parse →
   validate → execute, so a malformed query is 400 and a resolver that could not reach
   DynamoDB is 500. Every alert divides by HTTP status, so collapsing the two would make
   bad input look like an outage and let a real outage hide inside a 200. Resolver-level
   input rejections (a `javascript:` URL, an inverted date range) are tagged
   `gql.ErrInvalidInput` and mapped back to 400; one genuine backend failure among the
   errors makes the whole response 5xx, since under-reporting an outage is worse.
2. **`linkpulse_clicks_dropped_total` did not exist until the first drop.** A Prometheus
   `CounterVec` exports nothing until a label combination is observed, and
   `rate(missing_series[5m])` returns *no data* rather than zero — so the experiment-1
   alert could not have fired, and the panel would have read "No data", which during an
   incident is indistinguishable from a broken scrape. Fixed by seeding every closed label
   set at zero on startup. Open-ended sets (HTTP status, DynamoDB op) are deliberately not
   seeded, as that would invent cardinality.
3. **`Click.at` serialised as Go's native time format** (`2026-09-10 17:45:19.59 +0000
   UTC`) instead of RFC3339 — found by the phase-4 end-to-end run, not by a unit test.
   `Link.createdAt` had an explicit resolver and `Click.at` did not, so graphql-go's
   default resolver rendered the `time.Time` with `%v`. No client can parse that. Fixed,
   and `TestTimestampsAreRFC3339` now covers every time field in the schema.

### Tests written (all passing)

Each pins a property something downstream depends on:

- Shard sort keys are zero-padded so they order numerically; unpadded, `STAT#day#10` would
  sort before `STAT#day#2` and the A5 range scan would **silently undercount** with no
  error anywhere.
- The A5 `BETWEEN` bounds cover every shard of the range's last day and exclude the next.
- Click sort keys sort chronologically, and 500 clicks in the same instant produce no
  duplicate key.
- Write-sharding spreads: 400 clicks over 16 shards hit ≥10 distinct keys, over 1 shard hit
  exactly 1, and both report the same total.
- The redirect returns before the click is written, and the click still lands after drain.
- A full click queue sheds instead of blocking the redirect.
- Store outage → redirect 503 (not 404 — a cached negative answer would outlive the
  outage), GraphQL 500, readiness 503, liveness **200**.
- `/metrics` exposes the exact series names the alert rules and chaos assertions query.
- `/debug/leak` unreachable by default.
- No subscription root — the cut is load-bearing for the HPA and drain story, so it is
  enforced rather than remembered.
- Every schema time field is RFC3339.

---

## Phase 3 — Containerisation ✅ (verified: image built, tests run inside the build)

**`app/graphql-api/Dockerfile`** — multi-stage, `gcr.io/distroless/static:nonroot`, user
`65532:65532`, static `CGO_ENABLED=0` binary, `-trimpath -ldflags "-s -w"`.

- `--platform=$BUILDPLATFORM` on the builder with `GOARCH=${TARGETARCH}`: Go
  cross-compiles, so multi-arch needs no QEMU emulation of a native compiler.
- Module download is its own layer, so an application change does not re-resolve deps.
- `go test ./...` runs **inside the build**, so a broken commit cannot produce an image.
- `BREAK_READINESS` build arg produces experiment 2's broken image as a distinct artifact;
  the default is empty so a normal build cannot emit it by accident.
- No `HEALTHCHECK`: Kubernetes owns the probes, and distroless has no shell to run one.

---

## Phase 4 — Infrastructure as Code ✅ (verified: applied, idempotent, end-to-end)

Five modules, four environments, all four formatted and valid, and the local one **actually
applied against LocalStack with the application running on top of it**.

### Modules

| Module | Contents |
|---|---|
| `dynamodb-table` | the single table: PROVISIONED 25/25, sparse GSI1, TTL on `expiresAt`, capacity validated at ≤25 at plan time |
| `s3-state` | state bucket: versioned, SSE-S3, public access blocked, ACLs disabled, TLS-only policy, noncurrent-version expiry |
| `iam` | least-privilege policy derived from A1–A7, optional IRSA role, optional user |
| `vpc` | 2 AZs, public/private tiers, IGW, per-AZ route tables, optional NAT, free S3+DynamoDB gateway endpoints, locked default SG, NACLs |
| `eks` | control plane, OIDC provider, managed node group, explicit addons |

### Environments — four, not two

The plan specified `envs/aws` and `envs/local`. I split it into four, and the extra two
each earn their place:

- **`envs/bootstrap`** — creates the state bucket, keeps **local** state. The bucket cannot
  hold the state describing itself before it exists. The alternative is creating it by hand
  and having an untracked resource in the account.
- **`envs/aws`** — always-free only: table, IAM, VPC, budget guardrails.
- **`envs/aws-burst`** — the metered stack (EKS + IRSA), **separate state file**. This is a
  safety property, not tidiness: ending the burst is a `terraform destroy` that is
  *incapable* of deleting the table, because the table is not in that state file. Splitting
  the state is what makes the most dangerous command in the project safe to run in a hurry.
  It reads the base environment through `terraform_remote_state` rather than duplicating
  subnet IDs.
- **`envs/local`** — LocalStack. Excludes `vpc` and `eks` (LocalStack community does not
  implement EKS) and the budget guardrails (nor AWS Budgets), and says so in the file.
  Naming the exclusions is more honest than a "local" environment that is quietly a
  different shape from the real one.

### Decisions closed in this phase

**S3 native locking, not a DynamoDB lock table** — a deliberate deviation from the plan.
The plan called for the classic "S3 + DynamoDB lock table"; `use_lockfile` has since
replaced it. Two reasons, and the second is specific to this project: **a lock table would
eat the free tier the application needs.** The 25 RCU/WCU always-free allowance is *per
account*, not per table, so a second table means either the app table drops below 25 WCU or
the account starts billing — and experiment 1 depends on 25 WCU being the app table's
ceiling alone. A lock table would literally have cost me the throttle experiment.

**`map_public_ip_on_launch` is unconditional.** The IPv4 charge is per *assigned* address,
not per subnet setting, so an empty public subnet with auto-assign on bills zero. Making it
a variable would only mean the burst runbook has to flip a setting in the always-free
environment under a metered clock.

**Nodes in the public tier during the burst.** NAT is ~$3.24 over 72 hours plus data
processing; two public IPv4 addresses are ~$0.72. The private tier still exists and is
documented, and the free DynamoDB gateway endpoint means the application's data path never
crosses the internet either way.

**ON_DEMAND nodes, not SPOT.** Spot would save roughly $2 over the window. The window
exists to capture evidence, and an interruption mid-capture costs more than the saving.
Cost engineering means knowing which savings are not worth taking.

**The access key is not a Terraform resource.** `aws_iam_access_key` writes the secret into
state in plaintext, and state is a file that gets copied and versioned. The IAM user is
managed; its key is created out of band and lands as a Sealed Secret.

**Two budgets, not one.** A $1 tripwire (the always-free account should sit at $0.00, so any
charge means something unintended is running) and a separate $25 burst ceiling. During the
burst the tripwire fires continuously by design, which is exactly why the second budget
exists. Both carry FORECASTED notifications as well as ACTUAL, because ACTUAL is inherently
late — NAT accrues for hours before crossing a cent.

**mise tasks are the task runner; the Makefile forwards to them.** Phase 0 found `make`
absent on this host, and it cannot be installed through mise — so a Makefile-based workflow
would make `make` a third prerequisite and break the plan's two-prerequisite claim. Tasks
live in `mise.toml`; the `Makefile` forwards each target to `mise run <task>` for hosts that
have make. One implementation, two entry points. Every task runs in a container, so none of
them need Go, Terraform or Python on the host.

**Tool containers are compose services, not hand-written `docker run` lines.** Compose
resolves the relative `.` bind mount portably; a hand-written mount needs `C:/`-style paths
on Windows and `/`-style everywhere else — precisely the host dependency this project
claims not to have.

### The provider/LocalStack incompatibility — bisected, not worked around

The first `terraform apply` against LocalStack failed with `waiting for update AWS DynamoDB
Table (linkpulse): couldn't find resource (21 retries)` **on a table that was sitting there
ACTIVE**. Worth recording because the wrong fix was tempting and I nearly shipped it.

What the investigation actually established, in order:

1. The table, GSI, TTL and capacity were all correct in LocalStack, and a second container
   querying `DescribeTable` got `ACTIVE` back. So the config was not at fault.
2. Bisecting the resource: **every** variant failed, including a bare table with no GSI, no
   TTL and no PITR. So it was not my module.
3. Provider 5.100 created the same table in 0s → a provider-6 regression, not a LocalStack
   bug in general.
4. Bisecting provider 6.x: **6.12.0 last good, 6.13.0 first bad.**
5. I pinned every environment to `~> 6.12.0` and reverted the GSI to the deprecated
   `hash_key`/`range_key` (the `key_schema` blocks that replace them do not exist in 6.12).
   That worked — 11 resources applied, no drift.
6. **Then I checked whether a newer LocalStack fixed it, and it does.** LocalStack 4.14 +
   provider 6.64 + `key_schema` all work. So the pin came back off, the current provider is
   in use everywhere, and the non-deprecated syntax is back.

The real constraint is a floor on LocalStack, not a ceiling on the provider: **provider
≥6.13 requires LocalStack ≥4.14.** Both pins are now load-bearing and both carry the
reasoning inline, because dropping LocalStack back would break `terraform apply` in CI
while leaving real AWS fine — the worst-shaped failure to debug.

Two related findings:

- **`localstack:latest` is unusable here.** LocalStack has moved to date-based tags
  (2026.x) that require a licence and exit on startup without one. The 4.x line is the last
  reporting `edition: community`, so `4.14` is pinned as both floor and ceiling.
- **A current provider reads bucket tags through S3 Control**, which addresses hosts as
  `<account-id>.<host>`. The provider was resolving `000000000000.localstack` and getting
  NXDOMAIN — and before the `s3control` endpoint override, it was sending
  `ListTagsForResource` to **real AWS** and getting AccessDenied back from Amazon. It failed
  safe (credentials are `test`), but an environment named "local" silently talking to a real
  endpoint is not something to leave in place. Fixed with the endpoint override plus a
  `000000000000.localstack` network alias on the container.

Also removed: a no-op `server_side_encryption { enabled = false }` block. DynamoDB always
encrypts at rest with an AWS-owned key at no cost; the block only selects a customer-managed
key, so declaring it changed nothing while implying encryption was off.

### Verification evidence

Every claim below was run, not reasoned about.

```
# static: all four environments
scripts/tf-check.sh  →  fmt clean; init + validate Success ×4

# applied against LocalStack 4.14 with AWS provider 6.64
terraform apply   →  Apply complete! Resources: 11 added, 0 changed, 0 destroyed
terraform plan    →  exit 0  (no drift; idempotent)

# the table matches docs/data-model.md exactly
keys      [('PK','HASH'), ('SK','RANGE')]
capacity  25 RCU / 25 WCU
GSI1      [('GSI1PK','HASH'), ('GSI1SK','RANGE')]  INCLUDE ['longUrl','active']  5/5
TTL       ENABLED on expiresAt

# the application, on the Terraform-created table
readyz    {"clickShards":16,"queueDepth":0,"status":"ready"}
smoke.py  30/30 assertions PASS  (A1-A7, probes, metrics, RFC3339, feed ordering)

# write-sharding, both sides of experiment 1's config flip, identical image
CLICK_SHARDS=16  →  60 clicks over 16 distinct aggregate keys, sum 60
CLICK_SHARDS=1   →  120 clicks over 1 aggregate key, sum 120   ← the hot partition
linkpulse_click_shards gauge reports 16 / 1 respectively

# task wrappers
make -n dev verify smoke  →  forwards to mise run <task>
docker compose run --rm gotools   →  gofmt/vet/test clean
docker compose run --rm smoke     →  PASS
docker compose up -d --wait       →  localstack + grafana-renderer both Healthy
```

**`scripts/smoke.py`** is new and is the phase's most reusable artifact: stdlib-only, 30
assertions across every access pattern, non-zero exit on failure. It catches the class of
bug unit tests structurally cannot — a key built one way in code and another in Terraform,
an IAM policy missing the index ARN, a timestamp in the wrong format (which is exactly how
bug 3 above was found). It becomes the CI end-to-end gate and the post-chaos health check.

Also fixed while verifying: the Grafana renderer healthcheck probed `localhost` (which
resolves to `::1` in that image, while the renderer binds IPv4 only) and piped `wget` into
`head`, so it reported the exit status of `head` — always 0. A healthcheck that cannot fail
is worse than none.

### Toolchain pinned

`.tool-versions` now exists with exact versions, all verified to be real releases: golang
1.27.1, terraform 1.16.2, kubectl 1.37.0, helm 4.3.0, k3d 5.9.0, argocd 3.5.2, k6 2.2.0, jq
1.8.2, trivy 0.74.0, awscli 2.36.42.

Note the deliberate two-number split: `.tool-versions` pins Go **1.27.1** (what we build
with) while `go.mod` declares `go 1.25.0` (the minimum the module needs, forced by
`prometheus/client_golang`). The module says what it requires; the toolchain file says what
we use.

---

## Phase 5 — Kubernetes ✅ (verified: cluster rebuilt from scratch, manifests applied, smoke green through the ingress)

Three nodes, two overlays, and a pipeline. The application now runs the way it will run on
EKS, and the only file that knows the difference is thirty lines long.

### The cluster — `k8s/k3d/cluster.yaml`

Declared, not assembled from flags, because the cluster is infrastructure and gets a
reviewable definition like the rest of it.

| Property | Value | Why |
|---|---|---|
| Nodes | 1 server + 2 agents | the minimum at which drain, PDBs, topology spread and node-loss are statements about Kubernetes rather than about Docker |
| Version | `rancher/k3s:v1.34.5-k3s1` | the same minor EKS runs, so manifests meet the same API version in both places |
| Network | joins the existing `linkpulse` compose network | the cluster has to reach LocalStack |
| Ports | `127.0.0.1:8088→80`, `8443→443` | 8080 is taken by the compose `api`, and the two are expected to run at once |
| Extra SAN | `k3d-linkpulse-serverlb` | there is no host kubectl; every client is a container addressing the API server by container name |

### Manifests — `k8s/manifests/{base,overlays/local,overlays/aws}`

Kustomize, not Helm: one application and two environments does not justify a templating
language to substitute four values, and ArgoCD renders kustomize natively in phase 6.

**The base is the AWS shape and the overlays add to it**, not the other way round. `config.env`
sets no `DDB_ENDPOINT` because empty means real AWS — so the local overlay is the one that
has to say something extra. Inverted, production correctness would depend on an override
being remembered.

The aws overlay is thirty lines and that is the deliverable: an ingress class, an IRSA
annotation, and the absence of a Secret. Nothing about the workload differs.

### Decisions closed in this phase

**The Deployment declares no `replicas`.** An HPA and a committed replica count are two
controllers writing the same field. Under GitOps that is not a style question: ArgoCD would
see the HPA's value as drift, reset it, the HPA would scale it back, and the Application
would oscillate between Synced and OutOfSync forever. Omitting the field makes
`hpa.yaml`'s `minReplicas` the single committed source of truth. Verified: the first apply
creates one pod, and the HPA takes it to two within ~30s.

**The ConfigMap is generated with a content hash, not committed as an object.** This is the
highest-leverage decision in the phase and it is about experiment 1, not tidiness. A static
ConfigMap keeps its name when its contents change, so the pod template stays byte-identical
and nothing restarts — and this process reads its environment once at startup. Chaos
experiment 1 demonstrates the write-sharding mitigation by flipping `CLICK_SHARDS` from 16
to 1 in git. With a static ConfigMap that commit would sync green in ArgoCD, change nothing
at all, and the experiment would "prove" the mitigation while testing the same running
configuration twice.

**No CPU limit; memory limit yes.** A CPU limit is enforced by CFS quota, which throttles at
the quota boundary even on an idle node — on a latency-sensitive redirect path that appears
as p99 spikes with no matching load, one of the most commonly misdiagnosed symptoms in
Kubernetes. Requests already guarantee a share under contention. Memory is limited because
memory is not compressible: there is no throttle, only the OOM killer, so an unbounded leak
takes the node rather than the pod — and experiment 4 depends on that boundary existing.

**`preStop` uses the native `sleep` action, not `exec: ["sleep", "5"]`.** The image is
distroless: no shell, no `sleep` binary. The exec form — the one every tutorial shows —
would fail on every single pod termination. The native action needs Kubernetes ≥1.32, which
is below both targets. It closes the real gap: endpoint removal and SIGTERM are dispatched
concurrently, so without a pause the process starts shutting down while Traefik and
kube-proxy still route to it.

**PDB is `maxUnavailable: 1`, not `minAvailable: 2`.** A PDB is evaluated against the pods
that currently exist, not against the HPA's floor. With `minAvailable: 2` and the HPA at its
minimum of 2, the budget would allow zero voluntary disruptions — `kubectl drain` would
block forever and experiment 5 would deadlock instead of demonstrating anything. That
mistake is common in production and it presents as a hung cluster.

**`maxUnavailable: 0` with `progressDeadlineSeconds: 120`.** Together these are what make
experiment 2 a *stalled* rollout rather than an outage: the broken image adds a pod that
never becomes ready, capacity never drops, and after 120s the Deployment reports
ProgressDeadlineExceeded — which is what ArgoCD reads as Degraded and what the experiment
asserts on. Without the deadline the rollout hangs silently and reports nothing.

**`linkpulse.localtest.me`, plus a hostless ingress rule.** localtest.me and all its
subdomains resolve to 127.0.0.1 from public DNS, so the browser path needs no `/etc/hosts`
edit — which would need administrator rights and is exactly the host mutation this project
claims not to require. The second rule has no `host` and therefore matches any Host header,
which is how the smoke test reaches the ingress from inside a container, where
`localtest.me` would resolve to that container's own loopback. A YAML anchor keeps the two
rule bodies from drifting.

**Pod Security Admission set to `restricted`, enforced.** The container already satisfies it
— non-root, no privilege escalation, all capabilities dropped, seccomp RuntimeDefault,
read-only root filesystem. Enforcing it means a future manifest that quietly drops one of
those is rejected by the API server rather than merely reviewed by me.

**How the cluster reaches LocalStack — rewritten once, and the first version is the
interesting part.** k3d writes CoreDNS records for every container on the Docker network at
cluster-create time, keyed by container name, so `linkpulse-localstack` resolved from a pod
and the first implementation used it. It worked, and it was wrong for two reasons: the
records are IP literals frozen at create time (recreate LocalStack — which `clean` does —
and readiness fails with a connection timeout naming no cause), and they are only written on
create and start, so bringing the cluster up before LocalStack means no record at all,
forever.

The obvious repair was a task that re-injects them. It was written, and then deleted:
k3d's only re-injection path is a cluster stop/start, and `k3d cluster start` was observed
exiting **fatal** while the load balancer failed to come back — with the cluster recovering
on its own a minute later. A non-zero exit from something that actually worked is the worst
possible behaviour for a task.

So the dependency was removed rather than repaired. LocalStack now has a pinned address
(`172.30.0.10` on an explicitly-subnetted network), and the local overlay declares a
**Service with no selector plus a hand-written EndpointSlice** pointing at it. Pinning the
address fixes the staleness; declaring the Service fixes the ordering, because those objects
are applied with the rest of the manifests and do not care when the cluster was created.
The payoff: `DDB_ENDPOINT` in the cluster is now `http://localstack:4566` — byte-identical
to what docker-compose.yml sets, so the inner loop and the cluster cannot drift on the one
setting most likely to.

The subnet is inside Docker's own default pool on purpose: if the range is taken on some
host, `compose up` fails immediately with "Pool overlaps with other one on this address
space" rather than starting something subtly misrouted.

**Every cluster task is a sequence of single tool invocations, never a script.** Forced, not
stylistic: the k3d image is built `FROM scratch` and contains no shell, no sed, not even
`/bin/sh`. Putting the script on the host instead would make a POSIX shell a third
prerequisite on Windows — the exact claim the task runner exists to protect. The one piece
of control flow used, `A || B`, behaves identically in `sh` and in `cmd`, which is why it is
the only one used. `cluster-up` is idempotent through `k3d cluster list <name> || k3d
cluster create`, and it does not mask real failures: only the existence check is allowed to
fail.

**The kubectl tool container runs as root.** k3d writes the kubeconfig mode 0600 owned by
root — correctly, it holds a cluster-admin client certificate — and the image's default
non-root user cannot read it, failing with a bare "permission denied" that looks like a
missing file. The alternative is relaxing the kubeconfig to 0644, which would leave a
cluster-admin credential world-readable on the host to save nothing.

### CI — `.github/workflows/ci.yml`

Five jobs: three static checks that need no credentials and therefore run identically on a
fork's pull request, then the image build, then end-to-end on a real cluster.

- **app** — gofmt (checked on output, since `gofmt -l` exits 0 either way), `go vet`,
  `go test -mod=readonly -race`. `-mod=readonly` is what proves `go.sum` is authoritative,
  which closes a loose end left open in phase 4.
- **terraform** — the same container and the same `scripts/tf-check.sh` a developer runs, not
  a re-implementation in YAML.
- **manifests** — renders *both* overlays. This is the only thing that checks the aws overlay
  at all before the metered burst.
- **image** — multi-arch amd64+arm64, pushed to `ghcr.io/${{ github.repository }}` by long
  SHA, branch, semver and `latest`. `packages: write` is scoped to this job rather than
  granted file-wide, so the static jobs cannot write packages. Push is skipped on pull
  requests; the build still runs, so a broken Dockerfile fails a PR.
- **e2e** — LocalStack, the table created *by Terraform* (a hand-rolled table would make the
  key-mismatch class of bug structurally invisible), a real three-node k3d cluster, the
  manifests, an assertion that the HPA reached two ready replicas, and the smoke suite
  through Traefik. Diagnostics run under `if: always()`, because the interesting run is the
  failed one.

The image is built locally and side-loaded for e2e rather than pulled from ghcr.io, so the
job runs on a fork's pull request where no image was pushed — and so it does not have to
wait for the build job.

**Since verified, partly.** The workflow has now run in Actions. Every job but `e2e` passed
on the first attempt — the action versions and the ghcr.io push path are exercised. `e2e`
failed on runner disk rather than on anything it was testing; see the loose end at the end of
this file for the diagnosis and the fix.

### Bugs and findings

1. **`kubectl` was pinned three minor versions ahead of the cluster.** `.tool-versions` said
   1.37.0 with a comment citing the one-minor skew rule as the justification — while both
   the k3d cluster and the EKS target run 1.34. The comment argued against its own value.
   Now 1.34.5, matching the tool container.
2. **`kubectl wait --for=condition=Available` errors immediately on a resource that does not
   exist**, rather than waiting for it. k3s installs Traefik through a HelmChart CR, so the
   `traefik` Deployment is absent for the first minute — and the wait failed with
   `deployments.apps "traefik" not found` on a perfectly healthy cluster. Fixed with a
   preceding `--for=create`. Both `cluster-up` and CI carry the pair.
3. **`mise run clean` could wedge itself.** The k3d nodes are attached to the `linkpulse`
   network, so while they exist Docker refuses to remove it — and every later `compose run`
   that needs to recreate that network fails the same way, *including the task that would
   delete the cluster*. `clean` now runs `cluster-down` first. Found the hard way: changing
   the network definition made every compose command fail until the cluster was deleted with
   a raw `docker run`.
4. **A Compose service with no `networks:` key silently creates a second network.** The `k3d`
   service was creating `linkpulse_default` on every invocation.
5. **Git Bash rewrites absolute POSIX arguments before the process sees them.** `k3d
   kubeconfig write --output /repo/.kube/config` became
   `C:/…/Git/repo/.kube/config` — which, having lost its leading slash, was treated as
   *relative* and created a directory literally named `C:` inside the repository. No error;
   the next step then read a stale file at the real path and appeared to work.

   The same trap was then found in `tf-check`, written back in phase 4: `docker compose run
   … terraform /repo/scripts/tf-check.sh` reaches the container as
   `C:/…/Git/repo/scripts/tf-check.sh` and fails with "No such file or directory", naming a
   path nobody wrote. Both now pass paths relative to the service's `working_dir`, which
   nothing rewrites. This only bites when a task is run by hand from Git Bash — mise uses
   `cmd` on Windows — but that is exactly how these get debugged.

### Verification evidence

Every line below was run, not reasoned about. The cluster was deleted and rebuilt from the
committed config for this run, on a Docker network recreated with the pinned subnet.

```
# the cluster, from k8s/k3d/cluster.yaml
k3d-linkpulse-agent-0    Ready   <none>          v1.34.5+k3s1
k3d-linkpulse-agent-1    Ready   <none>          v1.34.5+k3s1
k3d-linkpulse-server-0   Ready   control-plane   v1.34.5+k3s1

# kubectl reaching the API server by container name, with no host kubectl and no
# --insecure-skip-tls-verify
docker compose run --rm kubectl get nodes   →  3 Ready

# the manifests
kubectl apply -k k8s/manifests/overlays/local
  namespace, serviceaccount, configmap (hashed), secret (hashed), service/linkpulse,
  service/localstack, deployment, pdb, hpa, endpointslice/localstack, ingress
rollout status  →  deployment "linkpulse" successfully rolled out

# the HPA owns the replica count — the Deployment declares none
hpa   Deployment/linkpulse   cpu: 1%/70%   min 2   max 6   replicas 2

# the PDB is satisfiable rather than deadlocked
2 healthy / 1 desired, disruptionsAllowed=1

# topology spread put the two replicas on different nodes
linkpulse-567b49654f-br76t   k3d-linkpulse-agent-1    ready
linkpulse-567b49654f-z8bz8   k3d-linkpulse-server-0   ready

# the out-of-cluster dependency, addressed by a selectorless Service
endpointslice/localstack   172.30.0.10:4566   ready=true
DDB_ENDPOINT=http://localstack:4566          ← identical to docker-compose.yml

# ingress
linkpulse   traefik   linkpulse.localtest.me   172.30.0.6,172.30.0.7,172.30.0.8   80

# end to end THROUGH Traefik, against the Terraform-created table
scripts/smoke.py --base-url http://k3d-linkpulse-serverlb  →  smoke: PASS
  (every access pattern A1-A7, probes, metrics, RFC3339, feed ordering)

# both overlays render with no cluster and no credentials
kubectl kustomize overlays/local  →  9 objects
kubectl kustomize overlays/aws    →  8 objects, no Secret, ingressClassName: alb,
                                     IRSA annotation on the ServiceAccount

# task wrappers
make -n dev dev-compose cluster-up cluster-down cluster-info k8s-image k8s-apply \
        k8s-delete k8s-smoke k8s-render verify clean   →  forwards to mise run <task>
```

Readiness passing is itself the proof that the LocalStack path works end to end: `/readyz`
calls `DescribeTable`, so a pod reporting ready has resolved `localstack` through the
selectorless Service, reached 172.30.0.10, and had DynamoDB answer.

---

## Phase 6 — GitOps, the Trivy gate, Terraform in CI ✅ (verified: commit → sync → rollout → revert → rollback, on the real cluster)

The cluster is now driven from git. A commit rolls the pods; a reverted commit rolls them
back; a manual `kubectl delete` is undone in one second. And the pipeline refuses to
publish an image that has not passed the scanner, or whose Terraform does not apply cleanly
and idempotently.

### Layout — `gitops/argocd/`

| Path | What it is |
|---|---|
| `install/` | ArgoCD itself: the upstream v3.5.2 manifest vendored byte-for-byte, plus four patches (insecure server behind Traefik, 60s reconciliation, dex and notifications scaled to zero, an Ingress at `argocd.localtest.me:8088`) |
| `base/project.yaml` | the AppProject — the blast-radius boundary: two source repos, two destination namespaces, `Namespace` the only cluster-scoped kind |
| `apps/local/`, `apps/aws/` | the app-of-apps sets: project (wave -1) + the linkpulse Application (wave 0); aws is local with two JSON-patched fields |
| `bootstrap/local/`, `bootstrap/aws/` | the root Application a human applies once, plus — locally — the route to git |

Sync waves also run *inside* the application now: namespace and the LocalStack Service at
-1, workload at 0, Ingress / HPA / PDB at 1. Order is a property of the manifests, not a
step in a runbook.

### Decisions closed in this phase

**Gitea in compose is the remote, not GitHub.** GitOps needs a repository the cluster can
reach, and "the GitHub repo" is the wrong answer for two of the three places this runs.
Offline there is no GitHub. In CI, chaos experiment 2 pushes a deliberately broken manifest
and reverts it — doing that against the real repository, from inside the pipeline that is
testing the real repository, is a loop nobody should build. A throwaway Gitea (pinned
address `172.30.0.11`, push-to-create, public repos so ArgoCD needs no credential Secret)
gives every environment its own remote. It sits on the same footing as LocalStack: dev
scaffolding, not part of the deployed system, which is why it lives in compose and is
reached from the cluster through the same selectorless-Service pattern.

**One repository, not two.** The plan (§4.7) says "a separate manifests repo". I kept one,
and the reason is the local loop: a second repository is a second thing to push to Gitea,
a second root Application, and a second bootstrap — for a separation whose benefit (CI
commits to manifests not retriggering application CI) a path filter provides. The
app-of-apps still points at a *path*, so splitting later is a `repoURL` change. Recorded as
a deviation, with the trigger to revisit: the moment a second team needs write access to
manifests but not to code.

**The upstream install manifest is vendored.** Referencing it by URL was the first attempt
and it does not work here — the kubectl tool image ships no CA bundle (x509 on the fetch)
and no git (kustomize's fallback). Vendoring also makes the install reproducible with no
network at all, at the cost of 1.9 MB of generated YAML that is never hand-edited. Every
change to ArgoCD's shape is a patch on top of the untouched file. Not Helm: the chart is a
second version to pin, and kustomize is already what everything else here uses.

**`kubectl apply --server-side` for the install, and it is required, not preferred.** The
ApplicationSet CRD is larger than the 256 KB limit on the `last-applied-configuration`
annotation client-side apply writes; a plain apply installs everything *except that CRD*
and exits non-zero with "metadata.annotations: Too long". Found by running it.

**`automated: {prune, selfHeal}` on every Application, and the root app has the
`resources-finalizer`.** Prune is what makes a deleted manifest a deleted resource
(verified: the superseded ConfigMap was gone after each rollout). SelfHeal is the property
behind "rollback is a reverted commit, never a manual patch": a manual patch does not
stick, so it cannot become the fix nobody wrote down (verified: 1s). The finalizer makes
deletion cascade, so teardown is one command.

**No `ignoreDifferences` for `spec.replicas`.** The Deployment declares no replicas field,
so ArgoCD's three-way diff has nothing to compare against the HPA's value — verified:
ArgoCD adopted the kubectl-applied resources as **Synced with no diff** at bootstrap. An
ignore here would hide the case where someone puts the field back and the oscillation
starts.

**The Trivy gate is `--ignore-unfixed`.** A finding with no available fix cannot be
addressed by a rebuild, so gating on it blocks every deploy until upstream ships a patch.
Reported, not gated. Everything with a fix is gated — and the distroless base is what makes
that a bar the image actually clears (0 findings across 6 OS packages and the binary)
rather than a permanent exception list.

**Terraform in CI is apply → plan → destroy, and the middle step is the test.** A plan
straight after an apply that is not empty means a resource fights its own definition, and
every future apply would "change" it forever. This is the class of failure that surfaced
the provider-6.13/LocalStack regression in phase 4. Destroy is exercised because it is the
command that runs under the most pressure — the end of the metered burst — and so the one
that must be known to work.

**`mise run gitops` is a second path, not a replacement for `dev`.** `dev` deploys with
kubectl and stays the fast loop; `gitops` is the real one. Same overlay rendered either way,
so ArgoCD adopts what kubectl applied with no diff.

### Bugs and findings

1. **`git revert -q` is not a flag**, and the first rollback "succeeded" in one second
   because the revert never happened and HEAD had not moved. Caught because the pods still
   carried the flipped ConfigMap hash. A reminder that the assertion has to be on the
   cluster, not on the exit code of the thing that was supposed to change it.
2. **`make gitops` printed "is up to date" and did nothing** — a directory named `gitops/`
   exists, and the target had not landed in `.PHONY` because an edit had missed. This is
   exactly the failure the Makefile's own comment warns about, demonstrated.
3. **A Windows clone with `core.autocrlf=true` would corrupt every script.** Git's warnings
   during the Gitea push showed the conversion in progress. `scripts/tf-check.sh` with CRLF
   fails in busybox `sh` on line one with an error that names nothing visible. Added
   `.gitattributes` with `* text=auto eol=lf`; one rule, whole class gone.
4. **Kustomize refuses a file reference outside the kustomization directory** ("security;
   file is not in or below"). Only directory bases cross that boundary, which is why
   `base/` and `bootstrap/base/` are directories with their own `kustomization.yaml` rather
   than single files.
5. **Application health flashed `Degraded` during the rollback**, for a few seconds while
   the old ReplicaSet's pods were terminating, then settled at `Healthy`. Transient, and
   not yet explained — noted here because experiment 2's assertion reads exactly that field,
   so the exploratory pass in phase 9 must characterise it before the assertion is written.

### Verification evidence

Every line below was run, not reasoned about, against the phase-5 cluster after a Docker
Desktop restart (the k3d nodes came back on their own; LocalStack did not, and Terraform
recreated the table on the next apply — 11 added, as designed).

```
# ArgoCD, from the vendored manifest
kubectl apply --server-side --force-conflicts -k gitops/argocd/install   →  61 objects
wait Available: applicationset-controller, dex (0/0), notifications (0/0), redis,
                repo-server, server                                        →  all met

# Gitea, push-to-create from a throwaway clone (NOT this repository's history)
git push http://localhost:3000/linkpulse/linkpulse.git   →  repo created, public, main

# bootstrap
kubectl apply -k gitops/argocd/bootstrap/local   →  service/gitea, endpointslice/gitea, application/root
wait --for=create application/linkpulse           →  created by root's first sync
NAME        SYNC     HEALTH    REVISION
linkpulse   Synced   Healthy   bab99cd   ← adopted kubectl-applied resources, no diff
root        Synced   Healthy   bab99cd

# the loop, forwards: commit CLICK_SHARDS 16 → 1
argocd saw the commit after 83s   (60s poll + jitter)
rollout: 1 of 2 updated … 1 old replica pending termination … successfully rolled out
configmap linkpulse-config-4k7552hdkf → linkpulse-config-6mh25f2b8m   (old one pruned)
/readyz  {"clickShards":1}   both pods on the new ConfigMap
  (the first scrape read 16 — the old pod, still serving through its preStop window)

# the loop, backwards: git revert, push
argocd saw the revert after 90s
rollout: successfully rolled out
configmap → linkpulse-config-4k7552hdkf   (6mh25 pruned)
/readyz  {"clickShards":16}
health: Degraded for a few seconds during termination → Healthy

# selfHeal
kubectl delete pdb linkpulse   →   recreated by ArgoCD after 1s

# Trivy gate
linkpulse:local (debian 13.6)  6 packages   0 vulnerabilities
server (gobinary)                           0 vulnerabilities   →  exit 0

# renders, no cluster: all seven kustomizations
k8s/manifests/overlays/{local,aws}
gitops/argocd/{install,apps/local,apps/aws,bootstrap/local,bootstrap/aws}   →  all OK

# task wrappers
make -n gitops scan gitea-up   →  forwards to mise run <task>   (after fixing .PHONY)
```

**Verified with a throwaway clone, not this repository.** This repo has no commits, and
committing on someone's behalf is not mine to do. Every push above came from a copy of the
working tree in a scratch directory with its own `.git`. `mise run gitops-push` pushes the
*real* HEAD; the first time it is run there has to be one.

---

## Phase 7 — Observability ✅ (verified: 29 assertions green, and a real outage detected, alerted, inhibited and resolved)

The cluster can now see itself. Every series the chaos experiments will assert on exists
and is scraped, every alert rule is loaded and evaluating, a Grafana panel renders to a PNG
headlessly, and application logs are queryable in Loki by JSON field — all proven by a
script, then proven again by pausing the datastore and watching the alerts fire.

### Layout — `monitoring/`

| Component | Version | Notes |
|---|---|---|
| Prometheus | v3.9.1 | annotation-driven pod discovery, kube-state-metrics, node-exporter, cAdvisor via the kubelet proxy; 15s scrape; 2d retention on emptyDir |
| Alertmanager | v0.29.0 | one Discord receiver, URL read from a mounted Secret file; two inhibit rules |
| Grafana | 12.3.0 | anonymous Viewer, three dashboards provisioned from committed JSON, datasources with fixed uids |
| Image renderer | 3.12.9 | **moved from compose into the cluster** — see below |
| Loki | 3.6.0 | single binary, filesystem, 48h retention |
| Alloy | v1.12.0 | DaemonSet tailing `/var/log/pods`; **replaces Promtail**, which reached EOL before this phase |
| kube-state-metrics | v2.17.0 | resources and RBAC limited to what the rules read |
| node-exporter | v1.10.0 | host namespaces; collectors limited to what is real inside a k3d node |
| CloudWatch exporter (yace) | v0.63.0 | **aws overlay only** — DynamoDB's own throttle and capacity metrics; IRSA role in `envs/aws-burst/cloudwatch.tf` |

Assembled with kustomize, one directory per component, `base/` + `overlays/{local,aws}`.
ArgoCD deploys it as the `monitoring` Application at sync wave 1, after the application.

### Alert rules — `monitoring/prometheus/rules/linkpulse.yml`

Thirteen rules in six groups: `general`, then one group per chaos experiment, so the rule an
experiment asserts on is findable by number. Each carries a `runbook` anchor into
`docs/runbook.md` (phase 11), named now so the runbook is written to match the alerts.
Thresholds are explicitly starting points — the plan says the committed assertions come
from observing each failure's real signature, and this phase's own outage test already
changed one rule (below).

### Decisions closed in this phase

**Plain manifests, no Prometheus Operator.** The operator is the idiomatic answer and what
most postings mean by "Prometheus on Kubernetes". It is also ~40 CRDs, a chart with a
thousand values, and an install that must succeed before the application's manifests can
even be applied. For one application on a cluster rebuilt weekly, the scrape config is 100
lines I can read and the alert rules are a file the chaos assertions cite by name. The
trigger to revisit is a second application needing scraping; the pod-template annotations
are already the shape the operator would discover.

**Alloy, not Promtail.** The plan named Promtail. It reached end of life in early 2026;
shipping an EOL agent in a portfolio about operating things well is the wrong signal.
Alloy is the same team's replacement, and the config does exactly what the Promtail one
would have — discover pods, tail `/var/log/pods`, relabel, push — in a different syntax.

**The image renderer moved from compose into the cluster.** Rendering is a round trip:
Grafana calls the renderer, and the renderer's Chromium calls *back* to Grafana to load the
panel. From a compose container that callback would cross into the cluster by hostname,
and `localtest.me` resolves to 127.0.0.1 — the renderer itself. In-cluster, both legs are
Service names, and the aws overlay deploys it identically. Removed from compose.

**Every ConfigMap is hash-generated** — scrape config, rules, dashboards, Alloy config, Loki
config. A rules edit that silently did nothing until someone restarted Prometheus is
exactly the kind of thing that makes an alert "not fire" during an experiment.

**`monitoring` is a `privileged` PSA namespace where `linkpulse` is `restricted`,** and the
difference is exactly two workloads: node-exporter (host namespaces, because the thing it
measures is the host) and Alloy (hostPath + root to read the kubelet's 0640 log files).
Everything else in the namespace would pass `restricted`. The profile is the namespace's,
so it is set by its most demanding member, and the label says so.

**The AppProject whitelist grew by two kinds** — `ClusterRole` and `ClusterRoleBinding` —
because Prometheus, kube-state-metrics and Alloy read the whole cluster. It was refused at
sync time until the project said otherwise, which is the project boundary doing its job.

**Alertmanager reads the Discord URL from a file, never from config.** The ConfigMap is
committed; the URL is a credential. Locally the Secret holds a placeholder — sends fail,
are logged, and the chaos assertions read `/api/v2/alerts` instead. `mise run
alerts-discord` writes a real one directly to the cluster, not to git.

**Dashboards are generated JSON, committed as JSON.** Three dashboards, 41 panels, stable
panel ids (the render URLs cite them). Per the dataviz guidance: one axis per panel, stat
tiles for headlines, status colours reserved for thresholds, series colours fixed to the
entity (`redirect` is always blue) rather than assigned by order. An annotation query on
`changes(linkpulse_click_shards[1m])` marks the experiment-1 flip on every time series.

**`scripts/observability-check.py` is the post-chaos gate.** Twenty-nine assertions: every
expected scrape job at its expected target count, every alert rule loaded and healthy,
twelve named series present with samples, no critical alert firing at rest, the
Alertmanager API answering with the Discord receiver loaded and no URL inlined, each
dashboard provisioned into the provider's folder, both datasources reachable, a panel
rendered to a PNG (checked by magic bytes), and application logs queryable by label and
JSON field. An experiment that leaves the stack unable to render or query has destroyed
its own evidence pipeline; this is what notices.

### The outage test

Pausing LocalStack (`docker compose pause`, so the table survives) is experiment 3 in
miniature, and it is what turned the alert rules from written into verified.

**First run** exposed two defects:

1. **`LinkpulseHighErrorRate` fired on a cluster with no user traffic.** With the store
   down, `/readyz` answers 503 *by design* — that is how the pod leaves the Service — and
   with nothing else calling, the probes were 100% of requests. The rule now excludes
   `route=~"healthz|readyz"` in both numerator and denominator, as does the dashboard.
   `LinkpulseNotReady` is the alert for that condition; the error-rate alert is for
   requests a user sent.
2. **kube-state-metrics was crash-looping**, so `LinkpulseNoReadyPods` fired for three
   evaluations and then vanished. Its liveness probe was `/livez` on the telemetry port,
   which returns 404 — the kubelet killed a healthy exporter every 45 seconds. `rollout
   status` had reported success because readiness passed before the first liveness kill;
   the check script had passed for the same reason. Every rule for experiments 2–5 reads
   this exporter. Fixed by moving `/livez` to the metrics port; the check in CI now runs
   after a deliberate 90s pause so liveness has had time to matter.

   And then the fix did not stick: `kubectl apply` corrected the live Deployment and
   ArgoCD's selfHeal reverted it within the minute, because Gitea still held the old
   manifest. Exactly the property phase 6 verified — a manual patch cannot become the fix
   nobody wrote down — experienced from the other side. The fix went through git.

**Second run**, with both fixes:

```
T+0     docker compose pause localstack
T+102s  LinkpulseNotReady            firing     (process view: linkpulse_ready == 0)
T+142s  LinkpulseNoReadyPods         firing     (Kubernetes view: kube_pod_status_ready)
        LinkpulseNotReady            inhibited  (by NoReadyPods, per the inhibit rule)
        pods: 0/1 Running, restarts=0          (liveness ignoring the dependency — as designed)
T+185s  docker compose unpause localstack
T+232s  both resolved
        pods: 1/1 Running, restarts=0
```

The 40-second gap between the two alerts is the probe pipeline: 2 failures × 5s for the
kubelet to mark NotReady, then kube-state-metrics' resync, then a 15s scrape and a 1m `for`.
That gap is what experiment 3's write-up will be about, and now there is a number for it.
Alertmanager's log shows the Discord delivery attempts against the placeholder URL: the
path to the receiver is exercised; only the last hop is stubbed.

### Verification evidence

```
# render, no cluster
monitoring/overlays/local   38 objects
monitoring/overlays/aws     39 objects   (+ cloudwatch-exporter)
gitops/argocd/apps/{local,aws}   3 objects each (project, linkpulse, monitoring)
terraform: envs/aws-burst with cloudwatch.tf   fmt clean, validate Success

# deployed, then adopted by ArgoCD from Gitea
kubectl apply -k monitoring/overlays/local   →  12 pods Running across 3 nodes
argocd: linkpulse Synced/Healthy, monitoring Synced/Healthy, root Synced/Healthy

# scripts/observability-check.py --ingress http://k3d-linkpulse-serverlb
Prometheus     targets: alertmanager=1 grafana=1 kube-state-metrics=1 kubernetes-cadvisor=3
                        kubernetes-pods=3 (2 app + traefik, free) loki=1 node-exporter=3 prometheus=1
               13 alerting rules, all healthy; 12/12 expected series present; 0 critical firing
Alertmanager   ready; discord receiver, URL from file, none inlined; /api/v2/alerts answers
Grafana        12.3.0; overview 18 panels, kubernetes 19, logs 4, all in folder LinkPulse
               datasources prometheus + loki OK; panel 1 → PNG, 11324 bytes
Loki           ready; {app="linkpulse"} | json | level!="" → 5 streams
observability-check: PASS (29 ok, 0 failed)     ← twice: before and after the KSM fix

# the outage test: see the timeline above
```

---

## Phase 8 — Automation, TLS, Sealed Secrets ✅ (verified: 31 TLS assertions green, a sealed round trip on the live cluster, a backup restored byte for byte after the table was destroyed — and a free-tier bug found by the new audit script)

Every ingress now serves a certificate from a CA the cluster owns, verified end to end
without `--insecure` anywhere. Secrets for the EKS overlays can sit in git encrypted. The
table can be exported, the table can be destroyed and recreated, and the export can be put
back and proven identical. And the account can be asked what it is spending — which is how
this phase found that "always-free" had a $3/month hole in it.

### Layout

| Path | What it is |
|---|---|
| `platform/cert-manager/` | cert-manager v1.21.1 vendored under `install/upstream/`, two patches (PSA `restricted`, resources); `issuers/` is the CA chain: self-signed bootstrap → `linkpulse-ca` Certificate (ECDSA P-256, 5 years, key never rotates) → `linkpulse-ca` ClusterIssuer |
| `platform/sealed-secrets/` | Sealed Secrets v0.40.0 vendored, moved from kube-system into its own `restricted` namespace; `sealing-cert.crt` is the committed public half of the key |
| `platform/aws-load-balancer-controller/` | Helm values for the one Helm install in the project (EKS only, see below) |
| `gitops/argocd/base/project-platform.yaml` | a second AppProject with the wider whitelist the platform needs (CRDs, webhooks, ClusterIssuer) |
| `gitops/argocd/apps/local/{cert-manager,sealed-secrets}.yaml` | wave −2 Applications; aws is the same two with the repoURL patched |
| `*/overlays/local/patch-ingress-tls.yaml`, `gitops/argocd/install/ingress.yaml` | `cert-manager.io/cluster-issuer` + a `tls:` block on all five ingresses |
| `monitoring/overlays/aws/sealed/` | the Discord webhook and Grafana password as SealedSecrets (placeholders, re-sealed before the burst) |
| `infra/terraform/envs/aws-burst/alb-controller.tf` | IRSA role + the controller's vendored IAM policy; nothing in EKS acts on `ingressClassName: alb` without it |
| `scripts/backup.py` | export / restore / verify, paced under the free capacity, botocore |
| `scripts/cost-report.py` | Cost Explorer by day × service and by `Environment` tag, plus every budget's actual vs limit, as markdown |
| `scripts/metered-resources.py` | everything billing by the hour (EKS, EC2, NAT, EIP, EBS, ALB/ELB) plus free-tier drift on the table; `--expect-none` makes it a gate |
| `scripts/tls-check.py` | 31 assertions: chain, SAN, issuer, expiry and routing for six names, plus a negative case |
| `scripts/seal.sh`, `scripts/seal-check.sh`, `scripts/burst-teardown.sh` | run inside the ops container; sealing offline, the sealed round trip, emptying the burst cluster before destroy |
| `scripts/bootstrap.sh`, `scripts/bootstrap.ps1` | the host step before mise exists: check Docker, get mise, `mise install`, pre-pull |
| `docker-compose.yml` `ops` | `alpine/k8s:1.34.5` — kubectl, kubeseal, helm, aws, python+botocore in one pinned image; `kubectl` now uses the same image |
| `mise.toml` | 20 new tasks: `platform-*`, `tls-*`, `seal*`, `backup*`, `cost-report`, `metered`, `tf-{bootstrap,aws,burst}-*`, `burst-*` |

`dev` now runs `platform-apply` before the application and ends with `tls-check` and
`seal-check`; `k8s-smoke` runs over HTTPS with the chain verified; `k8s-apply` waits for
the Ingress's certificate to be Ready before anything is asserted.

### Decisions closed in this phase

**cert-manager with an in-cluster CA, not mkcert.** The plan said mkcert, and `.gitignore`
still had the `certs/` entry for it. `mkcert -install` writes into the operating system's
trust store — precisely the host mutation §4.9 forbids, and the same reason the ingress
uses `localtest.me` instead of a hosts-file edit. It also leaves a CA private key on every
laptop and a script to re-run whenever a hostname is added. In-cluster, the CA key is a
Secret that never leaves the cluster, every Ingress asks for a certificate with one
annotation, renewal is cert-manager's job, and it is all declared in git and deployed by
ArgoCD. Trusting the CA on the host stays optional: `tls-ca` exports the public
certificate, and every check that proves TLS works passes it explicitly. Recorded as a
deviation from the plan, with the reason.

**The in-cluster name is in the certificate's SANs.** `k3d-linkpulse-serverlb` sits next
to `linkpulse.localtest.me` in the local overlay's `tls.hosts`. Traefik selects a
certificate by SNI, so without it the smoke test — which can only reach the ingress by
that name from inside a container — would get Traefik's default self-signed certificate
and have to skip verification. With it, `k8s-smoke` verifies the chain. The negative case
in `tls-check.py` (a name nobody issued for must be *refused*) is what proves the CA file
is doing the verifying rather than merely being present.

**A second AppProject, not a wider first one.** The `linkpulse` project's comment says an
Application in it "cannot install a CRD, a webhook or a cluster-wide admin binding however
its manifests are edited". cert-manager and Sealed Secrets are exactly those. Widening
the application's project to admit them would erase the boundary for the workloads it
exists to contain, so `platform` is a separate project with a wider, still enumerated
whitelist — and the extra permissions are a diff someone has to read.

**Application health restored in argocd-cm.** ArgoCD dropped the built-in health check for
`Application` resources in 1.8, which means the sync waves on the app-of-apps children
only ordered *creation*: the root app would have created cert-manager (−2) and linkpulse
(0) back to back, and "the platform before the application" was a fiction the annotations
told. The documented Lua snippet is now in `patch-cm.yaml`, and the waves are real.

**Sealed Secrets keys are backed up, not regenerated.** The controller generates its key
pair on first start, which is the right default and also the trap: a rebuilt cluster
cannot read anything sealed for the old key. `seal-export` backs the key up to `certs/`
(gitignored) and commits the public certificate; `platform-apply` restores the backup —
namespace first, key second, controller third — so that k3d after `clean` and EKS on
burst day both read what was sealed before they existed. Sealing is offline against the
committed certificate (`seal.sh`), so it needs no cluster at all. Verified below: a
controller starting with the restored key in place registered it and generated nothing.

**The aws overlay commits sealed *placeholders*.** The same non-values the local overlay
uses in plain text, sealed with my key. Committing them rather than nothing keeps
`monitoring/overlays/aws` renderable and applyable end to end (41 objects in CI's render),
so the shape of the aws deployment is exercised and the only thing the burst changes is
two encrypted values, re-sealed with real ones.

**One tool image for the cluster.** `kubectl` was `rancher/kubectl`, which ships kubectl
and nothing else — including no `aws`, which the EKS kubeconfig's exec plugin needs. Both
the `kubectl` and the new `ops` service are now `alpine/k8s:1.34.5` (the same kubectl
1.34.5, plus kubeseal, helm, the AWS CLI and a Python that carries botocore because the
CLI is built on it). The pay-off is that every task saying `docker compose run --rm
kubectl` drives EKS unchanged: the `burst-*` tasks set `LINKPULSE_KUBECONFIG` and reuse
`platform-apply`, `argocd-install` and the health waits that every local `dev` exercises.
Two versions in that image are deliberately not the `.tool-versions` pins (AWS CLI v1,
helm 4.1); neither difference reaches a command this project runs, and the compose file
says so.

**backup.py uses botocore, and is paced.** boto3 is a thin layer over the botocore client
API, so the calls read the same and the pinned image needs no pip step. The pacing is the
substantive decision: the table is provisioned at the free ceiling, so an unpaced Scan
would consume the read allowance in a burst and throttle the application it is meant to
protect, and an unpaced restore would be chaos experiment 1 by accident. Both directions
sleep according to the `ConsumedCapacity` every response reports — the service's own
accounting, not an estimate. And it runs as the operator: the application's IAM policy
*denies* Scan and BatchWrite, which is the point of the deny.

**The ALB controller is Helm, and not ArgoCD's.** The only Helm install in the project.
The controller's manifest install needs cert-manager for its webhook certificate *and*
cluster-specific values (name, VPC, role ARN) that only exist after `terraform apply` on
the burst; the chart generates its own certificate and takes the values as flags pasted
from `terraform output alb_controller_helm_args`. Making ArgoCD own it would mean
committing the account-specific ARN and VPC id, which nothing else in the repository does.
Its IAM policy is vendored at the chart's version, because a newer controller calling an
API the older policy does not grant fails as a 403 in a log rather than anything visible
on the Ingress.

**The burst teardown empties the cluster before Terraform destroys it.** `terraform
destroy` knows nothing about the ALB, target groups and security groups the controller
created from the Ingress. Destroy the cluster first and the controller is gone before it
can clean up: the ALB is orphaned at $0.0225/hour, and the only thing that would notice
is the $1 tripwire a day later. `burst-down` is therefore backup → delete the root
Application and wait for the cascade → wait until no load balancer carries the cluster's
tag → destroy → `metered-resources --expect-none` → cost report. The order is the safety
property.

**`bootstrap.sh` and `bootstrap.ps1` are the one deliberate duplicate.** The script's job
is to establish the task runner, so it cannot be a task; and a POSIX shell is not a
prerequisite on Windows. Four steps each, short enough to compare by eye, and neither
needs root or writes outside the user's home.

### Bugs and findings

1. **The always-free environment was not free.** `metered-resources.py`, on its first run
   against LocalStack, reported the table at 30 RCU / 30 WCU "including indexes, above
   the free 25". The module validated the table's capacity at ≤ 25 and the GSI added 5/5
   on top; `docs/data-model.md` even said index capacity "counts against the same
   always-free allowance" — the words were right and the numbers were not. Five units on
   each side is roughly $3 a month. Fixed at the source: the table is 20/20, GSI1 stays
   5/5, the module's validation now checks the *sum*, and the ceiling experiment 1 drives
   k6 past is 20 WCU (~10 clicks/sec, not ~12). The data model, the alert description and
   the Go comment all say 20 now, and the document records the correction. Applied to
   LocalStack as an in-place update; the plan afterwards was empty.
2. **`kubectl wait --for=create` with two resource names fails instead of waiting.** It
   resolved both names first and exited NotFound; one name at a time waits as documented.
   The `gitops` task waits for the platform Applications one per line.
3. **`kubectl wait --for=jsonpath='{...}'=<value>` cannot take a value containing `=`** —
   its parser splits on it, and base64 padding is an `=`. `seal-check.sh` reads the
   decrypted value with a go-template and compares in shell instead.
4. **BatchWriteItem reports `ConsumedCapacity` as a list**, one entry per table, where
   every other call returns a map. The pacer crashed on the first restore; found by the
   destroy-and-restore run, which is why that run exists.
5. **The 30-day expiry threshold in `tls-check.py` would have flaked at renewal.**
   cert-manager renews a 90-day leaf at 60 days, i.e. with exactly 30 days left. 14 now.
6. **Several files in the working tree have CRLF line endings** (the scratch-clone pushes
   list them). `.gitattributes` normalises them on commit, so what reaches Gitea and will
   reach GitHub is LF; the shell scripts among them are the ones that would break inside
   busybox, and none of the CRLF files is a script. Harmless, noted so the first real
   commit's warnings are not a surprise.
7. **`monitoring/overlays/aws` and `.gitignore` both still described the phase-8 secrets
   as arriving "sealed (kubeseal, phase 8)"** and the CA as something mkcert generates.
   Both updated to what exists.

### Verification evidence

Every line below was run, not reasoned about.

```
# platform, kubectl path, on the phase-7 cluster (no cert-manager present beforehand)
apply --server-side -k platform/cert-manager/install      →  47 objects
wait Available cert-manager, cainjector, webhook          →  met
wait caBundle on validatingwebhookconfiguration           →  met   (the precise "webhook is accepting" signal)
apply -k platform/cert-manager/issuers                    →  created first time, no retry needed
wait Ready certificate/linkpulse-ca                       →  met
apply -k platform/sealed-secrets/install                  →  12 objects, rollout complete
PSA `restricted` on both namespaces                       →  all four pods Running, 0 restarts

# GitOps: scratch clone pushed to Gitea, ArgoCD adopted the kubectl-applied platform
NAME             PROJECT     SYNC     HEALTH
cert-manager     platform    Synced   Healthy      ← adopted with no diff (server-side apply both ways)
sealed-secrets   platform    Synced   Healthy
linkpulse        linkpulse   Synced   Healthy      ← Ingress now carries the annotation and tls block
monitoring       linkpulse   Synced   Healthy
root             default     Synced   Healthy

# certificates, all issued by ArgoCD-applied Ingresses within a minute of sync
argocd/argocd-server-tls  cert-manager/linkpulse-ca  linkpulse/linkpulse-tls
monitoring/{grafana,prometheus,alertmanager}-tls              →  all READY True

# scripts/tls-check.py --ingress k3d-linkpulse-serverlb --ca certs/ca.crt
six names × (chain verified TLSv1.3, SAN contains name, issuer 'LinkPulse Local CA',
             expires in 89 days, GET routes 200)
nobody-issued-this.localtest.me  →  refused (self-signed certificate)   ← the negative case
tls-check: PASS (31 ok, 0 failed)

# scripts/smoke.py --base-url https://k3d-linkpulse-serverlb --ca-cert certs/ca.crt
smoke: PASS      (every access pattern, over TLS, chain verified, in-cluster name in the SAN)

# scripts/seal-check.sh (ops container)
live certificate fetched through the API server; fixture sealed; no plaintext in the sealed file
secret/seal-check data.value decrypts to 'hello'
secret garbage-collected with its SealedSecret (ownerReference)
seal-check: PASS
seal-export  →  certs/sealed-secrets-key.yaml (1 Secret), platform/sealed-secrets/sealing-cert.crt
seal.sh      →  monitoring/overlays/aws/sealed/{alertmanager-discord,grafana-admin}.yaml
kustomize monitoring/overlays/aws  →  41 objects (39 + 2 SealedSecrets)

# the key-restore path the burst depends on
delete key Secret → apply certs/sealed-secrets-key.yaml → rollout restart controller
controller log: "Searching for existing private keys" … "registered private key secretname=sealed-secrets-keyhcjb2"
seal-check: committed sealing-cert.crt matches the live cluster  →  PASS

# scripts/backup.py against LocalStack (ops container)
export   116 items in 2 pages, 2.5 RCU over 0.5s, manifest written
verify   backup 116 / table 116 / missing 0 / changed 0 / extra 0  →  PASS
terraform destroy (11 destroyed) → apply (11 added)              ← the table is gone and back, empty
verify   missing 91 (of 116; scan mid-restore)                    →  FAIL, correctly
restore  116 items in 8.1s, 119 WCU consumed (GSI writes included), 0 partial batches, paced at 15 WCU/s
verify   backup 116 / table 116 / missing 0 / changed 0 / extra 0  →  PASS
app pods after the table came back: 2/2 Running, 0 restarts     (liveness ignoring the dependency, as designed)

# scripts/metered-resources.py against LocalStack
7 hourly probes: InternalFailure each (eks/ec2/elbv2 not in LocalStack) → reported, not hidden
dynamodb: linkpulse 30 RCU / 30 WCU including indexes, above the free 25   ← finding 1
…after the fix: linkpulse 25 RCU / 25 WCU including indexes (free)
--expect-none with probes unreachable → "cannot certify none", exit 1 (a gate that cannot look must not pass)

# terraform
fmt clean; init + validate Success ×4 (aws-burst now including alb-controller.tf)
envs/local apply: read_capacity 25→20, write_capacity 25→20, 1 changed; plan -detailed-exitcode → 0

# static
shellcheck -s sh: tf-check.sh seal.sh seal-check.sh burst-teardown.sh bootstrap.sh  →  clean
py_compile: six scripts  →  ok
pwsh parser (mcr.microsoft.com/powershell:7.5): bootstrap.ps1  →  parse ok
kustomize: platform/cert-manager 50 objects, platform/sealed-secrets 12, apps/aws 6 (2 projects + 4 apps)
make -n (GNU make in a container): every new target forwards to mise run <target>
mise.toml: parses as TOML, 63 tasks

# the whole stack again, under ArgoCD, after the final push (rules description changed → Prometheus rolled)
observability-check: PASS (29 ok, 0 failed)
tls-check: PASS (31 ok, 0 failed)
smoke (https, verified): PASS
seal-check: PASS, committed certificate matches
```

**Not verified, and said so:** `cost-report.py`, `metered-resources.py`'s seven hourly
probes, the `tf-{bootstrap,aws,burst}-*` and `burst-*` tasks, `alb-controller.tf`, the
Helm install and `burst-teardown.sh` have never touched AWS — there are no credentials on
this machine. They are validated (terraform validate, shellcheck, py_compile) and every
individual command is one the k3d path runs or a plain AWS CLI read; the *sequences* are
what the burst runbook exercises first. Cost Explorer has no LocalStack at all.

Also verified in the background at the end of this phase: the cluster deleted and rebuilt
from scratch through the exact `dev` sequence with `platform-apply` in it, then ArgoCD
reinstalled and bootstrapped — recorded in the addendum below.


### Addendum — rebuilt from scratch

The cluster was deleted and the `dev` sequence run exactly as `mise.toml` orders it (mise
is not on this host, so each task's commands were run verbatim from a script), then the
`gitops` sequence on top. Timings from the log:

```
12:27:42  cluster-down                      k3d cluster deleted
12:28:53  cluster-up                        three nodes, Traefik/CoreDNS/metrics-server Available   (2m09s -- the addon images were warm this time)
12:31:02  platform-apply                    cert-manager Available, caBundle injected, issuers applied FIRST TIME (no retry),
                                            CA Ready; namespace, then certs/sealed-secrets-key.yaml restored ("secret/sealed-secrets-keyhcjb2 created"),
                                            then the controller                                              (1m32s)
12:32:48  k8s-apply                         rollout complete; certificate/linkpulse-tls created and Ready    (7s)
12:32:57  k8s-smoke (https, verified)       smoke: PASS
12:32:59  mon-apply                         twelve pods, all rolled out                                      (5m16s -- the pulls)
12:38:15  mon-check                         observability-check: PASS (29 ok, 0 failed)
12:38:21  tls-check                         FAIL (26 ok, 1 failed)   ← finding 8, below
12:40:17  tls-check --skip argocd           PASS (26 ok, 0 failed, 1 skipped)
12:40:19  seal-check                        PASS; "committed sealing-cert.crt matches the live cluster"  ← the restored key is the one in use
12:40:22  argocd-install + bootstrap        61 objects; root → 2 projects + 4 apps, all Synced/Healthy at 7db2e21 by 12:43:37
12:43:38  tls-check (all six)               PASS (31 ok, 0 failed)   -- argocd-server-tls issued 26s after the install
          seal-check                        PASS
```

Twelve and a half minutes from nothing to every check green, plus three for ArgoCD. The
restore of the sealing key before the controller's first start — the burst-day path — is
what the `seal-check` line proves: the fresh cluster's live certificate is the committed
one, so the sealed placeholders in the aws overlay would decrypt here without re-sealing.

8. **`tls-check` failed on the fresh cluster, on `argocd.localtest.me`, and was right to.**
   `dev` deploys with kubectl and never installs ArgoCD, so that Ingress and its
   certificate do not exist there; the earlier green run had ArgoCD on the cluster from
   phase 6. The script gained `--skip HOST` (validated against the list it checks, printed
   as skipped, never counted as passed); `dev` and CI's e2e job pass
   `--skip argocd.localtest.me`, the `gitops` task skips nothing. A check that had quietly
   treated "no certificate" as fine would not have caught this.

---


## Phase 9 — Load baseline and the five chaos experiments ✅ (verified: every experiment run against the real cluster, each leaving a timeline and rendered panels; two alert rules that could never have fired, found and fixed)

The alert rules were written in phase 7 with thresholds marked "starting points". This is
the phase that ran the failures they describe and found out. Two of the thirteen rules
were structurally incapable of firing — not wrong thresholds, wrong PromQL — and nothing
short of inducing the failure would have shown it. The other measurements are now numbers
in `docs/evidence/`, produced by scripts rather than read off a screen.

### Layout

| Path | What it is |
|---|---|
| `load-testing/k6/baseline.js` | two open-model scenarios (20 redirects/s over 25 links + 2 GraphQL/s), thresholds calibrated from the observed run; the export is committed under `docs/evidence/k6/` |
| `load-testing/k6/throttle.js` | experiment 1's traffic: every redirect on one link; `P99_MS` for the local allowance |
| `scripts/chaos/lib/chaos.py` | the harness: Prometheus/Alertmanager/kubectl observation, `wait_for` with recorded timings, a `Run` that persists across phases and writes `timeline.md` + rendered panels |
| `scripts/chaos/experiment{1..5}.py` | one script per experiment, phases `pre` / `during` / `post` (5: `drain` / `loss`); injection between phases by whichever tool owns it |
| `scripts/chaos/experiment2-git.sh` | the commit and the revert, pushed to Gitea from the ops container |
| `scripts/k6-summary.py` | the one line quoted from a k6 export; exit 1 on a failed threshold |
| `docs/evidence/chaos-{1..5}/` | `timeline.md`, `run.json`, and the panels for each run below (committed) |
| `mise.toml` | `k6-baseline`, `k6-throttle`, `chaos-1`…`chaos-5`, `chaos-all` |
| `.github/workflows/ci.yml` | the e2e job now runs the baseline and experiments 3, 1, 4, 5 and uploads the evidence; 2 needs the GitOps path and runs from `chaos-2` on a `gitops` cluster |

### Decisions closed in this phase

**The harness observes the monitoring stack, never the thing under test.** Every
assertion goes through Prometheus, Alertmanager's API or the Kubernetes API, and every
experiment ends with `observability-check.py`. A failure that is real but that the stack
does not see is a failed experiment — the alerts are the deliverable and the chaos is how
they are exercised. This is exactly how the two dead rules were caught.

**Injection is done by whichever tool owns the fault, between phases.** `docker compose
pause` for LocalStack, `k3d node stop` for a node, git for a commit, a port-forward for
the leak, and — the one exception — LocalStack's runtime config API from inside
experiment 1, because timing the clean window against the injection needs one process.
The phase scripts stay pure observers with a persisted timeline; each phase is a separate
process so the mise task can put the injection between them without a shell.

**Experiment 1 is injected locally, and the write-up says so.** The exploratory pass
measured 78 WCU/s of writes against the 20 WCU table with zero throttles: LocalStack's
DynamoDB Local never enforces provisioned capacity, and the compose comment that said
`DYNAMODB_SHARE_DB` made it enforceable was wrong (finding 1). `ENABLE_CONFIG_UPDATES=1`
exposes `DYNAMODB_WRITE_ERROR_PROBABILITY` at runtime, and setting it to 0.5 makes half of
all PutItem/UpdateItem calls fail with a genuine `ProvisionedThroughputExceededException`
— the exception, the SDK retry, the drop, the counter, the alert and the panel are all
real; only DynamoDB's reason is not. The burst runs the same k6 traffic with
`CHAOS_INJECT=none` and asserts the identical signature on the real throttle.

**Local latency thresholds measure LocalStack, and are calibrated accordingly.** At 40
redirects/s DynamoDB Local's GetItem p99 reached a full second with the nodes 80% idle;
at 20/s the baseline settled at p50 12.9 ms, p95 105 ms, p99 180 ms. The baseline
thresholds are therefore p95 < 150 ms and p99 < 250 ms (the alert line), throttle.js
takes `P99_MS` (1000 locally), and experiment 1's latency assertion is *relative* —
redirect p99 during the throttle within 2× the clean window or under the alert line —
because the claim under test is "throttling does not slow redirects", not an absolute.
The burst re-calibrates; real DynamoDB answers in single-digit milliseconds.

**Experiment 5's target is fixed and prepared, not chosen.** The first attempt drained
whichever agent had a replica and took Alertmanager and Grafana with it — single-replica,
no PDB — and the harness went blind mid-phase. The second attempt picked the node with
the fewest singletons, but a name chosen at run time cannot reach `k3d node stop` without
shell substitution, which cmd on Windows lacks and `mise.toml` does not use. So the target
is `agent-0`, and the `pre` phase makes that fair: it cordons the node, deletes every
single-replica pod on it so its controller recreates it elsewhere, uncordons, and rolls
the deployment so a replica lands there. The drain half then uses `--pod-selector` to
evict only the application through the eviction API. The preparation is itself a finding
(4), and the write-up says a production cluster would make it unnecessary.

**`git revert`, never a manual rollback, in experiment 2.** The property from phase 6 —
"rollback is a reverted commit, never a manual patch" — is asserted rather than assumed:
the break and the revert are both commits to Gitea, and ArgoCD is what changes the
cluster in both directions.

### Bugs and findings

1. **LocalStack does not enforce provisioned throughput, and the compose file said it
   did.** Measured, not assumed; comment corrected; injection added (above).
2. **`LinkpulseMemoryNearLimit` could never fire.** It divided
   `container_memory_working_set_bytes` (cAdvisor: `id`, `image`, `instance`) by
   `kube_pod_container_resource_limits` (kube-state-metrics: `node`, `uid`) with a bare
   `/`, which matches only identical label sets — so it returned no series on a cluster
   where both inputs had two. Found by querying the expression before experiment 4 ran.
   Fixed with `/ on (namespace, pod, container) group_left`; fired at T+94s in the run.
3. **`LinkpulseBelowMinReplicas` could never fire either.** `deployment` on one side,
   `horizontalpodautoscaler` on the other, bare `<`. Experiment 5's first run held the
   deployment at one ready replica for five minutes — the comparison was true the whole
   time and the alert did not exist. Fixed with `< on (namespace)`; fired at T+174s in the
   rerun. Both rules had loaded "healthy" in phase 7's check, which tests that a rule
   parses and evaluates, not that it can ever be true — that check now has a known gap,
   and the experiments are what close it.
4. **Draining a node takes the monitoring stack and the ingress controller down with
   it.** Traefik, CoreDNS, Prometheus, Alertmanager, kube-state-metrics, Grafana and Loki
   are all single replicas with no PodDisruptionBudget, and Prometheus keeps its two days
   of series on an emptyDir — a reschedule is a wipe. Locally acceptable and now
   documented; the experiment moves them off the target first. On any real cluster this
   is the first thing to fix after the application's own budget.
5. **`git revert -q` is not a flag** — the exact bug phase 6 recorded, made again in
   `experiment2-git.sh`. This time `set -e` made it fail loudly before anything could be
   asserted, rather than "succeeding" in a second; the assertion is still on the cluster.
6. **A single hot link at 40/s on LocalStack gives p99 ~250 ms with no fault at all**
   (DynamoDB Local serialising on the key), which is what made the absolute threshold
   wrong and the relative one right.
7. **Experiment 4 ran before finding 3 was fixed**, so its `belowMinReplicasAlertSeen`
   fact is `False` for a rule that was broken at the time, not because the condition did
   not occur (the pod was NotReady for the whole crash loop). Left as recorded; the CI run
   regenerates it.

### The measurements

Every number below is from the committed `docs/evidence/chaos-N/timeline.md` of the run
described; T+ is from the experiment's `pre` phase.

```
k6 baseline (2m, 20 redirects/s + 2 graphql/s, 2667 requests)
  redirect p50 12.9ms  p95 105ms  p99 180ms   graphql p95 160ms   failed 0.00%   dropped iterations 0

experiment 1 -- throttle (k6 20/s on one link, 6m; injection at T+88s, lifted at T+154s)
  clean window        redirect p99 21ms, 20.0 clicks/s recorded, 0 dropped
  first drop          10s after injection
  LinkpulseClicksDropped{reason=throttled} firing   65s after injection (30s `for` + scrape)
  throttle window     redirect p99 25ms, 20.0 redirects/s, 7.7 throttled calls/s, 10.2 recorded/s, 5xx ratio 0
  drops back to 0     55s after the injection was lifted; alert resolved 20s later
  k6 verdict          7202 requests, p50 8.3ms p95 13.6ms p99 34.7ms, 0 failed, 0 non-302

experiment 2 -- bad deploy through GitOps
  T+ 84s   ArgoCD synced the bad commit; a linkpulse:bad pod exists, never Ready; both old pods Ready
  T+204s   Deployment Progressing=False, ProgressDeadlineExceeded   (120s deadline + sync)
  T+285s   LinkpulseRolloutStalled firing; ArgoCD Degraded            (1m `for`)
           no 5xx at any point; redirects served by the old pods throughout
  revert   ArgoCD synced it 87s after the push; bad pod gone, NewReplicaSetAvailable, alert resolved 22s later, Healthy
           109s from push to fully rolled back, nobody touched the cluster

experiment 3 -- datastore unreachable (LocalStack paused 92s)
  T+ 17s   linkpulse_ready == 0 on every pod (process view)
  T+ 92s   LinkpulseNotReady firing; 0 ready pods (Kubernetes view); LinkpulseNoReadyPods firing;
           LinkpulseNotReady INHIBITED by NoReadyPods -- one page, not three
           ingress answers 503 (no endpoints); pods Running, 0 restarts
  T+ 99s   2 ready pods, 5s after unpause; both alerts resolved 20s later; still 0 restarts

experiment 4 -- OOMKilled to CrashLoopBackOff (one pod, /debug/leak, 128Mi limit)
  T+  4s   100 MB retained;  T+19s working set > 85% (Prometheus);  T+94s LinkpulseMemoryNearLimit firing
  T+107s   +40 MB -> OOMKilled, restart 0 -> 1;  T+127s LinkpulseOOMKilled firing
  kills 2..5 on the restarted container; kubelet backoff 10/20/40/80s
  T+497s   LinkpulseCrashLooping firing after the 5th kill (the 80s backoff is the first to outlast the 1m `for`)
           the other replica served through the ingress at every check
  recovery T+597s Running and Ready; CrashLooping and MemoryNearLimit resolved at once; OOMKilled 5m later (increase[5m], by design)

experiment 5 -- node loss (agent-0; Prometheus moved off it first)
  drain    6s: PDB allowed the one eviction, replacement Ready on another node 7s in, redirects served, no alert (gap < 30s)
  stop     T+ 49s node NotReady;  T+135s KubeNodeNotReady firing AND LinkpulseBelowMinReplicas firing
           redirects served by the surviving replica throughout
           replacement Ready ~5 minutes after the stop (taint-based eviction, tolerationSeconds=300)
           below the HPA minimum for 285s by Prometheus's own samples (rerun; the first run's script-side
           figure of 215s undercounted, being timed from when the script noticed rather than from the drop)
  start    node Ready 7s after `k3d node start`; KubeNodeNotReady resolved 32s later; stranded pod cleaned; 2 ready; PDB back to 1
```

### Verification evidence

```
# static
py_compile: lib + five experiments + k6-summary        →  ok
shellcheck -s sh: experiment2-git.sh                   →  clean
mise.toml: 71 tasks, parses;  make -n chaos-all k6-baseline  →  forwards
ci.yml: parses; e2e steps now end baseline → chaos 3, 1, 4, 5 → evidence upload → backup round trip

# LocalStack write-error injection (the local lever)
POST /_localstack/config DYNAMODB_WRITE_ERROR_PROBABILITY=0.5 → {"value": 0.5}; =0 → {"value": 0.0}
78 WCU/s with no injection: 0 throttles (finding 1)

# alert rules, after the two fixes, on the live cluster
container_memory_working_set_bytes / on (...) group_left kube_pod_container_resource_limits  →  2 series (was 0)
kube_deployment_status_replicas_ready < bool on (namespace) kube_horizontalpodautoscaler_spec_min_replicas  →  1 series (was 0)
observability-check after each rules push               →  PASS (29 ok)

# the five experiments: every phase exit 0, every timeline and panel written -- the numbers above
# mon-check after each                                    →  PASS
```

**Not verified:** experiment 1 without injection (the burst), and the `chaos-*` tasks
through mise itself (mise is not on this host; each task's lines were run verbatim, as
for every earlier phase). Experiment 2 has no CI job: it needs Gitea and ArgoCD, which
the e2e job does not stand up — a `gitops` e2e job is the natural next addition and is
noted in the loose ends.

### Addendum — the rerun of experiment 5, and a clock

Experiment 5 was rerun once more after the below-minimum window was re-timed from
Prometheus (285s, above). In that rerun `KubeNodeNotReady` took **1616s** to clear in
Alertmanager after the node came back, against 32s in the run before. It was not
Alertmanager: afterwards every observer's `process_start_time_seconds` — Prometheus,
Alertmanager, Grafana — sat ~27 minutes *after* its container's recorded start, all by
the same offset. The Docker Desktop VM's clock jumped forward by that much during the
post phase (host and VM agree now; they evidently did not before), and an alert whose
`endsAt` was four minutes ahead became one that was thirty-one minutes ahead. Windows
Docker Desktop clock drift after a host sleep is a known hazard; it is now a known hazard
of running these experiments on it, and the committed chaos-5 timeline carries the
artefact with this explanation rather than being replaced by a cleaner run.

8. **The Docker Desktop VM clock can jump during a long experiment**, and every timing
   in the harness is wall-clock. A run whose numbers look impossible should be checked
   against `process_start_time_seconds` of a process known not to have restarted before
   the numbers are believed.

---


## Phase 11 — The documents ✅ (written: runbook, architecture, burst runbook, postmortem, case study, README; every anchor and link checked)

Written last, as the plan orders, so that every document describes what exists and
quotes what was measured rather than what was intended. Phase 10 is still blocked on an
account, so its runbook is the one deliverable here that describes something unrun — and
says so in its second paragraph.

### What was written

| Document | For whom | What it holds |
|---|---|---|
| `docs/runbook.md` | the person the alert pages | one entry per alert under the anchor the rule's annotation carries (all ten resolve); meaning, three checks, action, what not to do, and what the experiment measured, with links into `docs/evidence/`; plus graceful shutdown (cited by `main.go`) and "observability is down" |
| `docs/architecture.md` | the reviewer | the mermaid diagram; the request path; a layers/owners table; every trade-off another file cites this document for (schema in code and no subscriptions, public-tier nodes, the provider/LocalStack floor, the capacity split, containers-only, CA over mkcert, what is injected); a verified-where table |
| `docs/burst-runbook.md` | the operator, on the clock | budget and abort conditions decided now; an eleven-item free checklist (every placeholder substitution listed by file); the window hour by hour with the check that gates each step; the teardown order; what the runbook does not yet know |
| `docs/postmortem.md` | anyone who runs alerts | the two dead rules as a blameless postmortem: timeline, root cause, four contributing factors, action items (all done), and the lesson |
| `docs/case-study.md` | the portfolio reader | the system, the numbers, the findings in order of what each would have cost, what the constraints bought, what is not done, a reading order |
| `README.md` | the front page | run it in three commands; what is where; a status paragraph that claims exactly what is verified |
| `infra/terraform/modules/vpc/README.md` | cited twice by the module | the NAT-vs-public-tier cost table |

Two action items from the postmortem landed in code: the rules file's header now states
the policy for any rule that joins vectors, and `observability-check.py`'s docstring says
what "loaded and healthy" does not prove.

### Found while writing

1. **The IRSA trust policy named a service account that does not exist.**
   `envs/aws-burst/variables.tf` defaulted `service_account_name` to `linkpulse-api`;
   `k8s/manifests/base/serviceaccount.yaml` has always said `linkpulse`. On EKS the
   pod's token carries `system:serviceaccount:linkpulse:linkpulse`, the condition does not
   match, and every DynamoDB call is `AccessDenied` with nothing wrong on either side
   alone — the outputs file even warns that this failure "looks like a credentials problem
   inside the pod and like nothing at all from the Terraform side". Found while listing
   the substitutions for the burst runbook. Default corrected, comment records it, the
   placeholder ARN in the overlay corrected to the role name the module actually creates
   (`linkpulse-burst-irsa`). Validated; unexercised until the burst, like everything else
   in that environment.
2. **The monitoring ingresses have no `alb` class in the aws overlay**, so on EKS Grafana,
   Prometheus and Alertmanager are reachable only by port-forward unless a commit adds it.
   Left as a decision for the runbook's pre-window checklist rather than made now: an
   internet-facing Grafana with `admin/admin` behind a placeholder password is not
   something to add by default.

### Verification evidence

```
runbook anchors: 10 cited by the rules file, 10 present            →  none missing
relative links in docs/*.md and README.md                          →  all resolve
terraform fmt + validate after the IRSA default change             →  aws-burst valid
kustomize monitoring/overlays/local after the rules header edit    →  38 objects
py_compile observability-check.py                                  →  ok
```

The documents were not "verified" in the sense the earlier phases use — they are prose —
but every number in them was copied from a committed timeline or export, and every
command in the runbooks is a task that exists in `mise.toml` today.

---

## Remaining phases (plan §7)

| # | Phase | State |
|---|---|---|
| 5 | k3d multi-node cluster, manifests, probes, limits, HPA, PDB; Actions → ghcr.io | ✅ done |
| 6 | GitOps: ArgoCD app-of-apps + sync waves; Trivy gate; Terraform in CI vs LocalStack | ✅ done |
| 7 | Observability: Prometheus, Loki, Grafana JSON, CloudWatch → DynamoDB metrics, Discord alerts | ✅ done |
| 8 | Automation scripts: bootstrap, teardown, backup, cost report; TLS via local CA (+ Sealed Secrets) | ✅ done |
| 9 | k6 baseline, then chaos 1–5 exploratory → codified with observed thresholds | ✅ done |
| 10 | The AWS burst (72h, $25), runbook written **before** the clock starts | blocked — needs an AWS account; none configured here |
| 11 | postmortem, runbook, case study, README, architecture diagram | ✅ done |

## Known loose ends

- ~~`docs/architecture.md` does not exist yet.~~ **Closed in phase 11**, with
  `docs/runbook.md` (every anchor the rules cite), `docs/burst-runbook.md`,
  `docs/postmortem.md`, `docs/case-study.md`, `README.md` and the vpc module README.
- ~~**The repository has no commits and no remote.**~~ **Closed.** The tree is now on
  GitHub at `rishabh0111/linkpulse`, fifteen commits, and CI has run. What remains of this
  item is the `OWNER` substitutions in `gitops/argocd/{apps,bootstrap}/aws/kustomization.yaml`
  — five placeholders, still unsubstituted because the aws overlay has nothing to sync to
  until the burst. The `IMAGE_NAME` in CI needed no edit: it derives from
  `github.repository`, and the first run confirmed it resolved to `rishabh0111/linkpulse`.
- **The `mise run` path is unverified on this host** — mise is not installed here. Every
  underlying command was run directly and passes, including the whole phase-5 cluster
  sequence, and the Makefile was confirmed to parse and forward all twenty-odd targets using
  a container that has GNU make; but `mise run dev` itself has not been executed. First
  thing to check on a machine with mise. The `A || B` idiom (in `cluster-up`, and since
  phase 8 in `platform-apply` twice) is the one piece of control flow in any task and is
  the specific thing to watch on Windows, where mise runs tasks through `cmd` rather than
  `sh`. Phase 8 added two more shapes to watch there: output redirection (`tls-ca` and
  `seal-export` write files with `>`; the `-T` on those runs is what keeps the bytes LF)
  and `mise run <task> -- <args>` (`seal`, `restore`, `metered`), which relies on mise
  appending the arguments to the task's command. `scripts/bootstrap.ps1` has been parsed,
  never run. Since the CI runs, a
  fourth shape joins them: `A || (B && exit 1)` in `chaos-3` and `chaos-5`, verified in POSIX
  sh over all four pass/fail combinations but not in cmd — check that a failing `during` or
  `loss` phase still fails the task there, with the recovery having run.
- **EKS `kubernetes_version = "1.34"` is still unverified against the API**, though the
  second half of this item is now closed: the k3d cluster is pinned to
  `rancher/k3s:v1.34.5-k3s1` and `.tool-versions` pins kubectl to 1.34.5, so the manifests
  are exercised against the same minor version in both places. What remains is checking
  `aws eks describe-cluster-versions` before the window opens — 1.34 may be near the end of
  standard support by then, and if it has moved, three pins move together: the EKS module,
  `k8s/k3d/cluster.yaml`, and kubectl.
- **No AWS credentials in this environment**, so nothing has touched real AWS. Everything
  targets LocalStack — the plan's own fallback position, exercised earlier than intended.
  `envs/bootstrap`, `envs/aws` and `envs/aws-burst` are validated but never applied, and
  so is everything phase 8 wrote for the burst: the `tf-*` and `burst-*` tasks,
  `alb-controller.tf`, the Helm install, `burst-teardown.sh`, `cost-report.py` (Cost
  Explorer has no LocalStack at all; it also costs $0.01 per API request, two per run)
  and the seven hourly probes in `metered-resources.py`. The burst runbook has to treat
  their first execution as a step, not a formality. Two console actions Terraform cannot
  take are also in that runbook: activating `Environment` as a cost allocation tag (or the
  by-tag section of the cost report is empty) and, if the ALB is to serve HTTPS, importing
  a certificate into ACM.
- **The §4.9 claim is verified on one host, not "every machine I use".** `mise run dev` is
  defined and its constituent steps all pass here; it has not been run from a fresh clone on
  a second machine. The README must not claim multi-host reproducibility before that
  happens.
- ~~`go.sum` is committed as generated; CI should run with `-mod=readonly`.~~ **Closed in
  phase 5** — the `app` job runs `go test -mod=readonly -race ./...`.
- **Discord delivery is exercised only up to the placeholder.** Alertmanager's Discord
  receiver is configured and its delivery attempts are in the log, but no real webhook
  has received a message. `mise run alerts-discord` installs one; the first real alert in
  a Discord channel is a screenshot for the case study, not a correctness question.
- **The sealed placeholders in `monitoring/overlays/aws/sealed/` decrypt only with my
  key.** They were sealed against the certificate exported from this machine's cluster;
  `certs/sealed-secrets-key.yaml` is the matching private key and lives only here (and
  wherever it gets backed up to). Anyone else cloning the repository must run
  `seal-export` against their own cluster and re-seal — the aws overlay still renders and
  applies for them, but the two Secrets never materialise until they do. On burst day the
  key is restored into EKS before the controller starts (`platform-apply` does it), which
  is the path verified in phase 8.
- **The cluster CA is per cluster.** `certs/ca.crt` is exported, not committed, so a
  browser trust import is redone after `clean`. Deliberate: a committed CA would either be
  stale or would need its private key committed too.
- **The CloudWatch exporter has never run.** It is in the aws overlay with an IRSA role in
  Terraform, both validated, neither applied. LocalStack's DynamoDB emits no CloudWatch
  metrics, so there is nothing to test it against until the burst — where the graph it
  produces (WriteThrottleEvents against a ConsumedWriteCapacityUnits line sitting well
  under 25) is the single most informative artefact the project can capture.
- **`dev` now takes noticeably longer**: the monitoring stack is eight images, and the
  renderer alone is ~1 GB; phase 8 adds four more (three cert-manager, one Sealed Secrets)
  and the ~400 MB alpine/k8s tool image. Re-runs are cheap; the first pull on a cold machine is not.
  Pre-pulling on the host and `k3d image import`ing is the mitigation if it becomes a
  problem; not done yet because containerd pulls were acceptable on the second run here.
- **Experiment 2 has no CI job.** It needs Gitea and ArgoCD, which the e2e job does not
  stand up; it runs from `mise run chaos-2` on a cluster brought up by `gitops`. A second
  e2e job that takes the GitOps path (Gitea in compose, ArgoCD installed, root app
  bootstrapped, then `chaos-2`) is the natural addition, at the cost of a second 15-minute
  cluster per push. Not written because the local run is verified and the CI run would be
  a transcription of it -- the same reasoning as the promotion step below.
- **The chaos evidence committed under `docs/evidence/` is from this machine's runs**, on
  Docker Desktop for Windows, with LocalStack behind the table. The latency figures are
  LocalStack's (phase 9 explains), the node-loss timings are k3d's defaults (40s
  node-monitor-grace-period, 300s tolerationSeconds), and one timeline carries a clock
  artefact it explains. The burst produces the second set, against real DynamoDB and EKS,
  and the case study compares the two.
- **`observability-check.py` cannot tell a rule that can fire from one that cannot.** It
  asserts that every rule loaded and evaluates without error, which both of phase 9's
  dead rules did. A cheap addition would be to evaluate each rule's expression with its
  comparison stripped and require at least one series -- but "no series" is also the
  correct answer for a rule about a condition that is not currently present, so the check
  needs the experiments to be meaningful, and now it has them.
- **The CI → manifests promotion step does not exist yet.** Plan §4.6 ends the pipeline
  with "update manifests": CI writing the pushed image digest into
  `k8s/manifests/overlays/aws` and committing it, so that ArgoCD on EKS rolls a build out
  without a human editing a tag. The `image` job already exports the digest for exactly
  this. Not written because nothing could verify it — it needs a real remote to commit to
  and a real registry to have pushed to — and an unverifiable job that commits to `main`
  is a worse thing to ship than a documented gap. First item once the repository is on
  GitHub.
- **`.github/workflows/ci.yml` has run; `e2e` has failed twice, for two different reasons.**
  Three runs so far. In all three, the seven other jobs passed (app, terraform static,
  terraform-against-LocalStack apply/idempotent-plan/destroy, manifests, scripts, the Trivy
  gate, and the ghcr.io build-and-push), which closes the action versions, the registry login
  and the multi-arch build as unproven.

  **Runs 1 and 2 — the runner's disk.** The job died deploying the monitoring stack. Not the
  stack's fault: the three k3d nodes are containers on the runner's one filesystem, so
  containerd pulls every image once *per node* — eight monitoring images with a ~1 GB Grafana
  renderer among them, on top of cert-manager, Sealed Secrets, the k3s addons and LocalStack.
  kubelet crossed its ephemeral-storage eviction threshold (10% of nodefs; 3.84 GB, against
  2.69 GB available) on all three nodes, evicted the workload — including the two healthy
  `linkpulse` pods, which is why they show `Completed` — tainted every node `disk-pressure`,
  and the replacements then failed to schedule with `0/3 nodes are available: 3 node(s) had
  untolerated taint(s)`. The job spent forty minutes in `rollout status` waiting for
  deployments that could not come up, which was the second half of the bug: the failure was
  slow, and its evidence was an eviction message rather than anything about disk.

  This was the loose end below about `dev` being image-heavy, arriving on a machine with less
  slack than mine. Both halves fixed in `e2e`: a reclaim step before the cluster is built,
  dropping the ~25 GB of toolchains the job never opens (Android SDK, hosted tool cache, .NET,
  GHC/ghcup, Swift, PowerShell) plus the runner's preinstalled images, then a hard assertion
  of 20 GiB free that fails in seconds with the number in it. `df`, `docker system df` and the
  node conditions and taints are now in the `always()` diagnostics. **Verified by run 3:**
  14 GB free became 46 GB, the assertion passed at 45 GiB, and the monitoring stack deployed,
  was proven observing, and passed TLS and the Sealed Secrets round trip. 35 GB was still free
  after it. The job also failed in 13 minutes rather than 47.

  **Runs 3 and 4 — the k6 summary export, for two stacked reasons.** The load baseline
  itself was entirely green: 5284 checks, none failed, 0 dropped iterations, redirect
  p95 = 9.02 ms and p99 = 14.17 ms against thresholds of 150 and 250, graphql p95 = 20.41 ms
  against 500. What failed was `k6-summary.py`, with `FileNotFoundError` on the export k6 was
  told to write. The directory is gitignored, so it exists on my machine from earlier runs and
  never in CI — and **k6 does not treat a failed `--summary-export` as fatal**: it logs
  `failed to handle the end-of-test summary` and exits 0, so `bash -e` let the job walk on and
  the real cause surfaced one command later as a traceback about the wrong thing. Exactly the
  `/.kube` problem, and fixed the same way the repository already solved it: the directory is
  tracked via `.gitkeep`, with the exports still ignored. `k6-summary.py` now also reports a
  missing file as a diagnosis (exit 2) rather than a traceback, which covers experiment 1 and
  the `k6-throttle` task as well — both write to the same directory.

  Run 4 failed at the same step, and the improved message paid for itself immediately: the
  error was no longer `no such file` but **`permission denied`**. The `grafana/k6` image runs
  as its own unprivileged `k6` user (uid 12345), which exists only inside the container; a
  Linux bind mount keeps the host's ownership, so that user cannot write into a directory
  owned by the runner. Docker Desktop does not enforce the mapping the same way, which is why
  this worked on my machine and failed in CI — the same shape of bug as the write-sharding
  default, where the local environment was the more forgiving one. Fixed by running the
  service as `user: "0:0"`, which is what `kubectl` and `ops` already do in the same file and
  for the same class of reason; the compose comment records it. The cost is root-owned
  exports on Linux hosts, which are gitignored scratch that `clean` removes.

  **Verified here, not in CI:** the ignore rules (an export is still ignored, the `.gitkeep`
  is not); all three of `k6-summary.py`'s exits — 0 on the real numbers above, 1 on a failed
  threshold, 2 on a missing file; and the permission fix itself, reproduced directly against
  `grafana/k6:2.2.0` — uid 12345 is denied on a host-owned directory, `0:0` writes. (The first
  attempt to reproduce it on this Fedora host was confounded by SELinux denying the container
  even read access, which is a property of this machine and not of CI; the clean test isolates
  the uid.) That e2e completes end to end still needs a green run — the chaos experiments and
  the backup/restore step have never executed in CI.
  **Run 5 — `k3d image import` reported success after failing on every node.** A regression in
  the sense that `Deploy` had passed in runs 3 and 4; not caused by either fix. The default
  import mode stages the image as a tarball in a volume shared with a temporary tools node,
  and that import failed on all three nodes at once:

  ```
  ERRO failed to import images in node 'k3d-linkpulse-agent-0': ... 
       ctr: open /k3d/images/k3d-linkpulse-images-20260912173228.tar: no such file or directory
  INFO Successfully imported image(s)
  INFO Successfully imported 1 image(s) into 1 cluster(s)
  ```

  k3d printed both of those, in that order, and exited 0. Nothing noticed until the Deployment
  came up `ErrImageNeverPull` two minutes later against an `imagePullPolicy: Never`, and the
  step that failed was `Deploy` — three steps downstream of the actual fault. This is the same
  shape as the k6 bug in run 4 and it is worth stating as a property rather than as two
  incidents: **the tools this pipeline drives report success after partial failure, so a step
  that does not assert its own postcondition moves the failure downstream and disguises it.**
  The disk assertion was written for that reason before either of these was known.

  Fixed with `--mode direct`, which streams the image into each node's containerd over exec
  and so does not involve the tools node or the shared volume — the whole failing path — plus
  an assertion that `linkpulse:local` is actually present in `ctr -n k8s.io images ls` on every
  node, which stays regardless of mode. Node names are read from the cluster rather than
  written as literals. **Verified here:** the YAML parses, the step passes `bash -n`, `--mode
  direct` is a real flag in k3d 5.9.0 (`k3d image import --help`), and the node names the
  assertion will iterate match the container names k3d used in the run 5 log. **Not verified:**
  the assertion against a real cluster — this host cannot run the compose flow, because SELinux
  is enforcing and the repo is not labelled for container access.

  **Run 6 — the first three fixes held; experiment 4 was observing the wrong moment.** The
  import assertion printed `linkpulse:local present` on all three nodes, the load baseline
  and chaos 3 and 1 passed for the first time, and then chaos 4 failed. It OOM-killed its
  target six times, confirmed `OOMKilled` every time, watched the kubelet's backoff escalate
  (9 s, 24 s, 42 s, 57 s, 90 s, 177 s), and never saw `LinkpulseCrashLooping`.

  The rule was fine. The harness wasn't. `kill_once` ended by waiting for the container to
  restart, which is to say it waited *through* the backoff gap, and only then did the loop
  ask whether the alert was firing. But the rule is `kube_pod_container_status_waiting_reason
  {reason="CrashLoopBackOff"} == 1` with `for: 1m`: its condition is true only while the
  container is waiting. After the restart it is false, and the alert is on its way to
  resolved. The committed local evidence shows it passing on exactly that residual window —
  `after kill 5: waiting reason None` and `ok: LinkpulseCrashLooping firing` at the same
  second, 497 s. Locally one check happened to land before resolution; in CI none of six did,
  even with a 177 s gap that was nearly three times the rule's minute.

  This is phase 9's dead-rules finding one level up. Those were rules that could not fire.
  This was an experiment that could not reliably *see* a rule fire, and passed anyway. The
  module docstring had the intent right ("until the waiting reason has held for a minute");
  the code checked after the waiting reason was gone.

  Fixed by making the gap the observation window. `wait_crashloop_in_backoff` runs inside
  `kill_once` between the push over the limit and the wait for restart, returns `True` as
  soon as the alert fires, and returns `False` as soon as the container restarts (that gap
  was shorter than the minute, so kill again). The loop cap went from 6 kills to 8, since a
  loaded runner stretches the backoff. The assertion is renamed to what it now proves:
  *fired while the container was in backoff*. The runbook's "Observed" note and the case
  study both say that the committed chaos-4 figures come from the earlier method.

  **Verified here:** `py_compile`, plus two stubbed tests in the scratchpad (the cluster
  calls replaced, the real module imported). `wait_crashloop_in_backoff` returns `True` when
  the alert fires inside the gap, `False` when the restart comes first, `False` on timeout,
  and `True` if the alert is already firing on entry. The `during()` loop stops on the kill
  the alert fires on (2, 5 and 8 checked), gives up at 8 with the assertion `False`, and the
  code after the loop still runs. **Not verified:** against a cluster, and the chaos-4
  evidence has not been regenerated. That needs the author's machine, or run 7's uploaded
  artifact, and `killsToCrashLoopAlert` / `secondsToCrashLoopAlert` will likely move.

  **Found while waiting on run 7 — two chaos steps that could not fail.** Reading ahead at the
  steps no run had reached yet, looking for the same class as k6 and k3d, turned it up in this
  repository's own YAML. Chaos 3 and chaos 5 each wrapped their assertion phase as
  `during || docker compose unpause localstack` and `loss || k3d node start agent-0` — in
  `ci.yml` and in the `chaos-3` / `chaos-5` mise tasks both. The intent, stated in the mise
  comments, was cleanup: never leave the store paused or the node stopped. The effect was that
  the line takes the recovery command's exit status, so **a failed experiment was cleaned up and
  then reported as passed** — `bash -e` and mise's abort-on-failure both see 0. Checked against
  run 6's log before repeating any claim: chaos 3's `during` there was genuine (only `ok:` lines,
  no `chaos: FAIL`, and localstack unpaused once, not twice), so the pass stands. It would not
  have shown the next failure.

  Fixed differently in the two places, because they run under different shells. `ci.yml` is
  bash: `rc=0; phase || rc=$?; recover; test "$rc" -eq 0 || exit "$rc"`, which fails with the
  assertion's own code and says so in an `::error::`. mise has to stay cmd-compatible on Windows,
  where there is no `$?` substitution, so it uses `phase || (recover && exit 1)` — a subshell
  exiting 1 in sh, a group exiting 1 in cmd. **Verified here:** YAML and TOML parse, `bash -n`
  on both rewritten steps, and the exit semantics of both idioms directly — the mise form in POSIX
  `sh` over all four pass/fail combinations (0, 0, 1, 1), the old form reproducing the bug
  (`false || true` → 0), and the CI form under `bash -e` (recovery runs either way; exits 0 on a
  pass and with the phase's own code, 3, on a failure). **Not verified:** the cmd half of the mise
  form, which needs Windows — it joins the existing `A || B` item under the `mise run` loose end.

  **Run 7 — green.** All eight jobs, e2e in 53 min 28 s. Checked rather than read off the
  job list, because run 7 still had the old `loss || node start` line in chaos 5: the whole
  log has zero `FAIL`, zero `TIMEOUT` and zero `##[error]` lines; every check in chaos 5's
  `loss` phase is `ok:`; and there is exactly one k3d `Starting node` between `loss` and
  `post` (the `||` would have made it two). So every pass in run 7 was real.

  Experiment 4, under the new observation method, on a GitHub runner:

  | | local, old method | CI run 7, new method |
  |---|---|---|
  | first OOM kill | — | 94 s |
  | `LinkpulseOOMKilled` | 125 s | 99 s |
  | `LinkpulseCrashLooping` | kill 5, 495 s | kill 5, 335 s |
  | how it was seen | after the restart, before the alert resolved | **78 s into the backoff gap**, while the waiting reason held |

  The kill count did not move. The first kill carries no watch — it is the one that holds for
  `LinkpulseMemoryNearLimit` and `LinkpulseOOMKilled` — so three gaps were watched before the
  fifth: they closed at 39 s, 27 s and 57 s, each shorter than the rule's minute plus scrape
  and evaluation lag, and the harness said so and escalated each time. The fifth gap outlasted the minute
  and the alert fired inside it, which is what the experiment claims to show. The committed
  `docs/evidence/chaos-4/` is still the local run. It is not replaced: the committed evidence
  is the author's machine by design, and this run's copy is in the `chaos-evidence` artifact.
  The runbook and case study quote both.

  **Run 8 — green again, with the chaos 3/5 fix in.** All eight jobs, e2e in 54 min 30 s.
  Checked the same way as run 7, since a clean job list was the failure mode being fixed:
  zero `FAIL`, `TIMEOUT` and `##[error]` lines. The two `failed (exit` matches are both the
  step script echoed in the group header (the literal `$rc`), not the error firing.
  `linkpulse-localstack Unpaused` appears once, and k3d's `Starting node 'k3d-linkpulse-agent-0'`
  twice — once at cluster creation and once after `loss` — so each recovery ran exactly once
  and no assertion phase failed. Experiment 4 reproduced run 7: the alert fired **81 s into the
  fifth kill's backoff gap** (78 s in run 7), confirmed at 336 s. Two consecutive green runs
  are the evidence that the pipeline is stable rather than lucky once. The new rc-capture lines'
  failure path is covered by the local `bash -e` test above, not by a CI failure, which is the
  right way round.

- **The pinned Docker subnet `172.30.0.0/16` is a hardcoded choice** (docker-compose.yml,
  and the EndpointSlice literal in the local overlay). It is inside Docker's default pool so
  a collision fails loudly at `compose up` rather than misrouting, but it is the one value in
  this repository that could need changing on a different host. Both files say so, and it is
  the first thing to check if `dev` fails on a new machine.
- **`automountServiceAccountToken: false` alongside IRSA is reasoned, not observed.** The
  EKS pod identity webhook injects its own projected token volume, which should be
  unaffected — but that is an argument, and the burst is where it becomes a fact. It is
  cheap to check there and it is flagged in the Deployment.
- **k3s addon image pulls were observed taking up to five minutes each** on this
  connection (CoreDNS, Traefik, metrics-server, local-path-provisioner, pulled by containerd
  inside the nodes rather than by the host Docker). `cluster.yaml` therefore sets a 600s
  creation timeout, because a short one turns a slow network into a rolled-back cluster that
  was in fact working. Cold `mise run dev` is dominated by this; re-runs are about a minute.
