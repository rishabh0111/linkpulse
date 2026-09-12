# LinkPulse

A URL shortener with click analytics, built as an exercise in operating a service well on
a budget of **$0** and a host with **nothing installed but Docker and a task runner**.
The product is small on purpose; the work is the DynamoDB data model designed around its
own failure mode, Terraform that applies against a mock in CI and a real account on
demand, a three-node Kubernetes cluster deployed by GitOps with TLS from its own CA, a
monitoring stack whose alerts were proven by inducing the failures they describe, and
five chaos experiments that leave timelines and rendered panels behind.

Start with [docs/case-study.md](docs/case-study.md) for the story and the findings, or
[docs/architecture.md](docs/architecture.md) for the diagram.

## Run it

Prerequisites: Docker (Desktop or Engine, with Compose v2) and [mise](https://mise.jdx.dev).
`scripts/bootstrap.sh` (Linux/macOS) or `scripts\bootstrap.ps1` (Windows) checks the
first, installs the second into your home directory, and pre-pulls the tool images.
Nothing else is installed on the host — Go, Terraform, kubectl, k6 and Python all run in
pinned containers — and nothing system-wide is changed: no hosts file, no trust store, no
sudo.

```sh
sh scripts/bootstrap.sh       # or: powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
mise run dev                  # ~15 minutes cold (image pulls), ~1 minute warm
```

`dev` brings up LocalStack, applies the Terraform, creates the k3d cluster, installs the
platform (cert-manager's CA, Sealed Secrets), deploys the application and the monitoring
stack, and then **proves it**: the smoke test through the ingress over verified TLS, the
29-assertion observability check, the TLS check across every hostname, and the Sealed
Secrets round trip. A `dev` that ends at a prompt without checking anything is how a
broken environment gets mistaken for a working one.

```
dashboard     http://linkpulse.localtest.me:8088     https://linkpulse.localtest.me:8443
grafana       http://grafana.localtest.me:8088       (anonymous viewer; admin/admin to edit)
prometheus    http://prometheus.localtest.me:8088
alertmanager  http://alertmanager.localtest.me:8088
```

`localtest.me` resolves to `127.0.0.1` from public DNS, which is why no hosts-file entry
is needed. The `https://` names carry certificates from the cluster's CA; `mise run
tls-ca` exports it to `certs/ca.crt` if you want a browser to trust them — optional,
nothing here depends on it.

Then, the GitOps path and the experiments:

```sh
mise run gitops               # Gitea as the remote, ArgoCD deploying from it, prove Healthy and Synced
mise run k6-baseline          # two minutes of load, thresholds as the verdict
mise run chaos-3              # or chaos-1..5, or chaos-all — see docs/runbook.md for what each induces
mise tasks                    # everything else, with descriptions
```

`make <task>` forwards to `mise run <task>` on hosts that have make.

## What is where

| Path | What |
|---|---|
| `app/graphql-api/` | the Go service: redirect hot path, GraphQL, async click recorder, probes, metrics |
| `docs/data-model.md` | the single-table design, written before the code; the hot-partition argument |
| `infra/terraform/` | five modules, four environments (`local`, `bootstrap`, `aws`, `aws-burst`) |
| `k8s/` | the k3d cluster definition and the application manifests (base + `local`/`aws` overlays) |
| `platform/` | cert-manager with an in-cluster CA; Sealed Secrets; the ALB controller's Helm values |
| `monitoring/` | Prometheus, Alertmanager, Grafana (+ renderer), Loki, Alloy; 13 alert rules, 3 dashboards |
| `gitops/argocd/` | ArgoCD install (vendored), two AppProjects, the app-of-apps, the bootstrap |
| `scripts/` | smoke, observability and TLS checks; backup/restore; cost report; metered-resource audit; `chaos/` |
| `load-testing/k6/` | the baseline and the hot-link load |
| `docs/evidence/` | every chaos run's timeline and rendered panels; the k6 export |
| `docs/runbook.md` | what to do when each alert fires, with what the experiment measured |
| `docs/burst-runbook.md` | the 72-hour AWS window, written before it opens |
| `docs/postmortem.md` | the incident the project had with its own alert rules |
| `tracker.md` | every decision with its reasoning, every bug, every verification — the primary record |
| `mise.toml` / `Makefile` | the tasks; every one a single tool invocation in a container |

## Status, honestly

Everything above is verified on one machine (Windows 11, Docker Desktop), through the
exact commands in `mise.toml` — but not yet through `mise run` itself on a second
machine, and **nothing has touched a real AWS account**: the `aws` and `aws-burst`
environments, the load-balancer controller, the cost report and the teardown are
validated and unexecuted. CI has never run because the repository has no remote yet.
`tracker.md` § Known loose ends is the complete, current list; the README will not claim
more than it does.

## Findings worth reading about

Building this found: an "always-free" table that would have billed $3 a month (index
capacity counts); two alert rules that were syntactically fine, evaluating "healthy", and
incapable of ever firing; a LocalStack that never enforces the capacity the throttle
experiment needed; an IRSA trust policy naming a service account that did not exist; and
a monitoring stack that a routine `kubectl drain` takes down with the workload. Each is
in the case study, with how it was found and what changed.

## Third-party

The vendored upstream manifests under `*/upstream/` (ArgoCD, cert-manager, Sealed
Secrets) are unmodified copies of their releases and keep their own Apache-2.0 licences.
