# The Burst — Runbook

Seventy-two hours of metered AWS, budgeted at $25, to run the same manifests, the same
experiments and the same checks against real DynamoDB and EKS. This document is written
before the window opens, because metered time is for executing decisions, not making
them. Every command is a mise task that exists today; every placeholder substitution is
listed; every step has the thing to check before moving on.

Status: **not yet run**. Nothing below has touched a real account. The first execution
of each step is itself a step — expect the runbook to be corrected as it goes, and keep
the corrections.

## Budget and the numbers that gate it

| Item | Estimate | Source |
|---|---|---|
| EKS control plane | $0.10/h → $7.20 | `modules/eks` header |
| 2 × t3.small on-demand | ~$0.045/h → $3.23 | same |
| 2 × 20 GB gp3 | ~$0.26 | same |
| ALB | ~$0.0225/h → $1.62 | same |
| 2 public IPv4 | ~$0.72 | `modules/vpc` |
| Cost Explorer calls | $0.02 per `cost-report` run | `scripts/cost-report.py` |
| **Expected** | **~$13–15** | `terraform output estimated_72h_usd` says the compute part |
| Ceiling | $25 (`burst_budget_usd`) | `envs/aws/guardrails.tf` |

Abort conditions, decided now: the `linkpulse-burst-ceiling` budget's FORECASTED
notification at 100% means destroy today, not at the end of the window; ACTUAL at 80%
means finish the experiment in progress and destroy. `mise run cost-report` each morning
is the check; the budgets email is the backstop.

## Before the window: the checklist that costs nothing

Everything here is free-tier or free, and none of it starts the clock.

1. **Account and credentials.** A fresh account, or one at $0.00. An IAM user with
   `AdministratorAccess` for the operator; its key in the shell as `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`, `AWS_REGION=ap-south-1`. `mise run aws-whoami` prints the
   account id — write it down; it appears in every ARN below.
2. **Repository on GitHub.** The first real commit and push (this repository has none as
   of phase 11). Confirm the CI workflow runs green once on `main` — `.github/workflows/ci.yml`
   has never executed, and the `image` job is what publishes `ghcr.io/<owner>/linkpulse`
   by digest. Make the package public, or ArgoCD on EKS needs a pull secret this
   repository does not carry.
3. **Substitutions.** Placeholders, all account- or owner-specific, none committed with
   real values:
   - ~~`OWNER` → the GitHub owner~~ done: `gitops/argocd/{apps,bootstrap}/aws` point at
     `rishabh0111/linkpulse`.
   - The image in `k8s/manifests/overlays/aws/kustomization.yaml` needs no hand edit:
     CI's `promote` job pins it by **digest** (`newName:` + `digest:`) after every green
     run on `main`. Check it is not still `ghcr.io/owner/linkpulse` / `newTag: main`,
     and `git pull` first: each promotion is a bot commit on `main`.
   - `arn:aws:iam::000000000000:role/linkpulse-burst-irsa` →
     `terraform output irsa_role_arn` (after step 8) in
     `k8s/manifests/overlays/aws/patch-serviceaccount.yaml`.
   - `arn:aws:iam::000000000000:role/linkpulse-burst-cloudwatch-exporter` →
     `terraform output cloudwatch_exporter_role_arn` in
     `monitoring/overlays/aws/cloudwatch-exporter.yaml`.
   - `https://linkpulse.example.invalid` (`PUBLIC_BASE_URL`) and
     `https://grafana.linkpulse.example.invalid` → the ALB hostname, known only after the
     Ingress has one (step 11). Two commits, then; the first with the placeholder is
     harmless (redirect URLs are wrong, nothing else).
   These are commits to `main`; ArgoCD reads them. Do them on a branch, verify
   `mise run k8s-render`, merge.
4. **Terraform inputs.** `infra/terraform/envs/aws/terraform.tfvars` (`alert_email`),
   `envs/aws-burst/terraform.tfvars` (`state_bucket`, after step 5), and
   `infra/terraform/backend.hcl` (bucket + region). All three gitignored.
5. **State bucket.** `mise run tf-bootstrap-apply`. Copy the printed backend block into
   `backend.hcl`. Local state for this one environment stays in `envs/bootstrap/` —
   back it up somewhere; it is the only state with nowhere else to live.
6. **The always-free environment.** `mise run tf-aws-plan` (read it: it must create a
   table at 20/20 + 5/5, an IAM policy and user, a VPC with no NAT, two budgets, and
   nothing with an hourly price), then `mise run tf-aws-apply`. Then
   `mise run metered -- --expect-none` must pass and `terraform output billed_resources`
   must be an empty list. Leave this running for the whole credit window; it costs
   nothing.
7. **Cost allocation tag.** Billing console → Cost allocation tags → activate
   `Environment` and `Project`. A console step Terraform cannot take; until it is done
   `cost-report`'s by-environment section is empty. Activation is not retroactive.
8. **EKS version.** `aws eks describe-cluster-versions` — 1.34 must be in standard
   support. If not, the three pins move together: `modules/eks` (`kubernetes_version`),
   `k8s/k3d/cluster.yaml` and `.tool-versions`, and `mise run dev` is re-run locally first.
9. **Sealed Secrets key.** `certs/sealed-secrets-key.yaml` must exist (from `mise run
   seal-export` on the local cluster whose certificate is committed). Re-seal the two
   real values now: `mise run seal -- monitoring alertmanager-discord
   monitoring/overlays/aws/sealed/alertmanager-discord.yaml webhook-url=<real>` and the
   same for `grafana-admin`. Commit the sealed files. `platform-apply` restores the key
   into EKS before the controller starts (verified on k3d in phase 8).
10. **Local dress rehearsal.** `mise run clean && mise run gitops && mise run chaos-all`
    the day before. If the local run is not green, the burst is not the place to find out
    why.
11. **The runbook itself.** Read `docs/runbook.md` once more; it is what gets used when an
    alert fires at 2 am on day two.

## The window

Times are from `tf-burst-apply` returning. Every step names the check that gates the next.

**T+0 — cluster.** `mise run burst-up`: `aws-whoami`, `tf-burst-apply` (prompts; ~20
minutes; read the plan — it must NOT touch the table), `burst-kubeconfig` (nodes Ready),
`platform-apply` (cert-manager, the sealing key, the controller), metrics-server (EKS ships
none; without it the HPA reads `cpu: <unknown>`), `argocd-install`, the
`bootstrap/aws` root app, and a wait for `linkpulse` Healthy. Check: `mise run
argocd-status` shows five Applications Synced/Healthy at the substituted revision.
Record `terraform output` in full — `estimated_hourly_usd` is the burn rate to hold
against the cost report.

**T+30m — the ALB.** `mise run burst-alb-controller -- <terraform output
alb_controller_helm_args>`. Watch `kubectl get ingress -n linkpulse` until an ADDRESS
appears (a few minutes). Then the second substitution commit (`PUBLIC_BASE_URL`, Grafana
root URL). Check: `curl -I http://<alb>/healthz` → 200; `/readyz` → 200 (**this is the
IRSA check**: a 503 with `AccessDenied` in the logs means the trust policy's service
account name or the role ARN annotation is wrong — `modules/iam` and the phase-11 note in
`envs/aws-burst/variables.tf`). Optional: a certificate from the local CA imported into
ACM and attached with `alb.ingress.kubernetes.io/certificate-arn`, for HTTPS on the ALB;
without it the ALB serves HTTP and the checks are run with `http://`.

**T+1h — the checks that make the rest mean anything.** With
`LINKPULSE_KUBECONFIG=/repo/.kube/burst` and the ALB as the base:
`scripts/smoke.py --base-url http://<alb>`, `scripts/observability-check.py --ingress
http://<alb>` (Grafana and friends are behind the same ALB only if their Ingresses get
the alb class — the aws monitoring overlay does not do that today; port-forward with
`mise run burst-argocd-ui`-style tasks or add the class in a commit, decide before the
window). Check: smoke PASS; the CloudWatch exporter target is UP in Prometheus and
`aws_dynamodb_consumed_write_capacity_units_sum` has samples — **this series is the
single most informative artefact of the whole project** and it has never existed.

**T+2h — baseline.** `docker compose run --rm -e BASE_URL=http://<alb> k6 run
load-testing/k6/baseline.js --summary-export docs/evidence/k6/baseline-eks.json`. Expect
single-digit-millisecond p50 and a p99 well under the local 180 ms; commit the export
beside the local one. Then `mise run backup` — the first real export — and
`mise run backup-verify -- --in <file>`.

**T+3h — experiment 1, un-injected.** The one experiment that is different on AWS:
`CHAOS_INJECT=none`. Run `throttle.js` at 40/s for ten minutes (`-e RATE=40 -e
DURATION=10m`, no `P99_MS`) and the `during` phase beside it. Real DynamoDB throttles once
the burst bucket drains — expect the first `throttled` drop within about a minute and the
alert a minute after. Capture: the Overview panels, and the CloudWatch panel showing
`WriteThrottleEvents` rising against `ConsumedWriteCapacityUnits` pinned at 20. Then
flip `CLICK_SHARDS` to 1 in a commit, let ArgoCD roll it, and run again: the honest
expectation (`docs/data-model.md` §3, phase-9 notes) is that the throttle rate is the
*same*, because a 20 WCU table has one partition and the ceiling is the table's, not a
key's — and that is the finding to write up, not a failure of the mitigation. Revert.

**T+5h — experiments 3, 4, 5, 2.** Experiment 3's real-AWS form: detach the policy from
the IRSA role (`aws iam detach-role-policy --role-name $(terraform output -raw
irsa_role_name) --policy-arn $(terraform output -raw app_policy_arn)`), run the `during`
phase, re-attach, run `post`; STS caches credentials for up to fifteen minutes, so the
outage arrives later than the pause did locally — record when. Experiment 4 needs the
leak flag in the aws overlay for the window (a commit; remove it after). Experiment 5
on EKS: `aws ec2 stop-instances` on one node's instance id for the stop half; the node
group replaces it — expect a *longer* below-minimum window than k3d's 285 s because a
new instance has to join. Experiment 2 is unchanged except that the remote is GitHub.
Each experiment's evidence directory gets an `-eks` copy; keep the k3d ones.

**Every morning — cost.** `mise run cost-report -- --out docs/evidence/cost/day-N.md`
and `mise run metered`. The by-service table should show EKS, EC2 and ELB and nothing
else metered; DynamoDB at $0.00.

**T+72h, or earlier — teardown.** `mise run burst-down`: backup, then
`burst-teardown.sh` (deletes the root Application and waits for every ALB tagged for the
cluster to disappear — the step that prevents the classic orphaned-ALB bill), then
`tf-burst-destroy` (prompts; read the plan — it must NOT destroy the table), then
`metered -- --expect-none` (must pass), then a final `cost-report`. The always-free
environment stays. Check the next day's cost report reads $0.00 for the day.

Learned on 2026-09-24:

- **Keep the machine awake until the destroy returns.** Terraform deletes the node
  group and only then the cluster. The laptop slept four minutes into the node group's
  deletion. AWS finished that server-side within minutes, but `DeleteCluster` was not
  sent until the laptop woke 2 h 48 m later (CloudTrail: `DeleteNodegroup` 14:10:48 UTC,
  `DeleteCluster` 16:59:19 UTC), and the control plane billed the whole time. Disable
  sleep for the teardown, or run it from a machine that does not sleep.
- **Delete `/aws/eks/<cluster>/cluster` afterwards.** EKS creates the control-plane log
  group itself, with no retention, and `terraform destroy` does not know about it.
  `metered-resources.py` does not look at log groups, so this check is manual:
  `aws logs describe-log-groups`.
- The ALB the controller creates carries none of Terraform's default tags, so its cost
  lands under "(untagged)" in the by-environment report. An
  `alb.ingress.kubernetes.io/tags` annotation on the Ingress would attribute it.

## After the window

- Commit everything under `docs/evidence/*-eks` and `docs/evidence/cost/`.
- Compare: local vs EKS baseline latency, experiment timings, the experiment-1 sharding
  result. That comparison is the case study's centre.
- The tracker gets a phase-10 section in the same shape as the others: what was run,
  what deviated from this document, what it cost to the cent.
- If the account is to be closed: `terraform destroy` on `envs/aws`, then on
  `envs/bootstrap` (the state bucket has `force_destroy` off — empty it first), then
  the cost allocation tags and budgets are gone with it. LocalStack keeps the repository
  runnable afterwards, which was the design.

## What this runbook does not know

- Whether `aws eks get-token` from the ops image's AWS CLI v1 is accepted by EKS 1.34
  (it should be; it has not been tried).
- The exact ALB provisioning time and whether the security group the controller creates
  admits the node group's pods without a rule in `modules/eks` (IP target mode needs the
  ALB's SG to reach pod IPs; the controller manages this when `vpcId` is set — verify on
  the first `/healthz`).
- Whether STS credential caching makes experiment 3's detach take 5 or 15 minutes to
  bite. Record it; that number is part of the write-up.
