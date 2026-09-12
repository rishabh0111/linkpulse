# Postmortem: two alerts that could never fire

**Date of incident:** the rules were committed in phase 7 (2026-09-10) and shipped as
"verified"; the defect was found in phase 9 (2026-09-11) by inducing the failures they
described.
**Duration:** roughly a day of the project's life, across two phases, one cluster
rebuild and one GitOps adoption, during which the monitoring stack reported thirteen
healthy alert rules and had eleven.
**Severity:** in production terms, a silent monitoring gap on two failure modes — memory
exhaustion and running below the replica floor — that would have paged nobody.
**Status:** resolved; the experiments that found it now run in CI.

This is written as a blameless postmortem because that is the shape the failure
deserves, and because the project's claim is that it operates things well. The
interesting question is not who wrote the bad PromQL (I did) but why every check that
was supposed to catch it said "fine".

## Summary

Two of the thirteen alert rules in `monitoring/prometheus/rules/linkpulse.yml` compared
series from two different exporters with a bare binary operator:

```promql
container_memory_working_set_bytes{...} / kube_pod_container_resource_limits{...} > 0.85
kube_deployment_status_replicas_ready{...} < kube_horizontalpodautoscaler_spec_min_replicas{...}
```

PromQL matches vector operands on their **full label set** unless told otherwise.
cAdvisor stamps `id`, `image` and `instance` on the working set; kube-state-metrics stamps
`node` and `uid` on the limit; a Deployment series carries `deployment`, an HPA series
carries `horizontalpodautoscaler`. No pair of series ever had identical labels, so both
expressions were always empty: syntactically valid, evaluating without error, and
incapable of ever returning a row. Prometheus reported both rules as `health: ok`, the
observability check counted them among the thirteen loaded, the dashboard panels for the
same quantities worked (they plot the two sides separately), and nothing anywhere was
red.

## Impact

None on users — the application was never in either state during the window. On the
project: phase 7's tracker entry claimed "every series the chaos experiments will assert
on exists and is scraped, every alert rule is loaded and evaluating". True, and
insufficient. Had experiment 4 or 5 been run with a check that trusted the rule rather
than inducing the failure, the experiments would have failed at the assertion and the
first instinct would have been to loosen the timeout.

## Timeline

| When | What |
|---|---|
| Phase 7 | Rules written; thresholds marked "starting points"; the shape (which series, which labels) treated as settled. `observability-check.py` asserts 13 rules loaded and healthy: passes. |
| Phase 7 | The outage test pauses LocalStack and exercises `LinkpulseNotReady` / `LinkpulseNoReadyPods`, which use an explicit `* on (pod) group_left` join. Both fire. Confidence in the file rises. |
| Phase 8 | Cluster rebuilt from scratch; monitoring adopted by ArgoCD; `observability-check` green twice. |
| Phase 9, experiment 4, before running | The harness queries the memory expression to time its own wait and gets **0 series** where both inputs have 2. The rule is found dead before the experiment starts. Fixed: `/ on (namespace, pod, container) group_left`. |
| Phase 9, experiment 5, first run | Node stopped; `kube_deployment_status_replicas_ready` at 1 for five minutes; `LinkpulseBelowMinReplicas` never fires; the script times out. A range query over the window shows the inputs correct and the comparison true throughout. Fixed: `< on (namespace)`. |
| Phase 9, reruns | Both alerts fire at the expected points (T+94s and T+174s). Findings 2 and 3 recorded. |

## Root cause

A vector-to-vector operator without an `on`/`ignoring` clause, between series that
could never share a label set. Two instances of one mistake.

## Contributing factors

1. **"Loaded and healthy" was read as "works".** Prometheus's rule health only says the
   expression evaluates without error. An expression that matches nothing is the
   healthiest kind: it never errors and never fires.
2. **The one outage test exercised the rules that happened to be right.** The phase-7
   test hit the dependency-outage group, whose join was explicit because it needed
   `group_left` to bring in a pod label. The two dead rules looked simpler and were
   written without one.
3. **The dashboards plotted the same quantities correctly**, because a panel with two
   series does not divide them. Seeing the memory panel work made the memory alert feel
   verified by proximity.
4. **The check that would have caught it was structurally impossible in phase 7.** A
   rule about a condition that is not present *should* return no series. The only way to
   distinguish "correctly quiet" from "incapable" is to make the condition present — and
   that is what phase 9 exists to do. The gap was in sequencing expectations, not tooling:
   phase 7 should have said "loaded; not yet proven to fire".

## What went well

- The experiments were designed to observe the monitoring stack rather than the
  workload, so a real failure that the stack did not see was a failing experiment rather
  than a passing one with a note.
- The harness queried the rule's expression *before* injecting anything (experiment 4),
  which turned a would-be timeout into a diagnosis with zero rows and two inputs to
  compare.
- The fix was a one-line change in the rule file, pushed through git, rolled by ArgoCD,
  and re-verified by re-running the experiment — the same path any production fix would
  take.

## Action items

| # | Action | Status |
|---|---|---|
| 1 | Fix both rules with explicit joins, with a comment saying what they were and how it was found | done, `monitoring/prometheus/rules/linkpulse.yml` |
| 2 | Run experiments 4 and 5 in CI so the rules are exercised on every push | done, `.github/workflows/ci.yml` e2e job |
| 3 | Record in `observability-check.py`'s docstring that "loaded and healthy" is not "can fire", and that the experiments are the check for that | done |
| 4 | Audit the rules file for every remaining vector-to-vector operator | done: the three others all carry `on (...)`; `sum(...) / sum(...)` forms are scalar-shaped and match by construction |
| 5 | For any future rule that joins exporters: write the `on` clause first, then query the expression against the live cluster with the comparison stripped and require a row before committing | policy, in the rules file header |

## Lessons

An alert is a claim about the future; the only test of one is the failure it describes.
"The rule is loaded" and "the panel shows the number" are both true of a rule that will
never page anyone. This project's chaos experiments were planned as demonstrations of
resilience; their first real result was finding two holes in the net that was supposed to
catch the demonstrations. That is a better result than the one that was planned, and it
is the reason the experiments run on every push now rather than once for a screenshot.
