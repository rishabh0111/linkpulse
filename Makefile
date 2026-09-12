# Convenience wrapper. The real task definitions live in mise.toml.
#
# Why this file is a forwarder and not the implementation: GNU make cannot be installed
# through mise and is absent by default on Windows, so making it the implementation would
# turn `make` into a third host prerequisite and break the claim that Docker and mise are
# the only two. mise can run every task on every host, so mise owns the definitions and
# this file exists for the muscle memory of typing `make dev`.
#
# On a host without make:
#
#     mise run dev
#     mise run verify
#     mise tasks            # list everything, with descriptions
#
# Both paths execute the same commands. There is no second implementation to drift.

MISE ?= mise

# Every target is phony: these are commands, not files, and without this a directory
# named `dev` or a file named `clean` would silently stop the target from running.
.PHONY: help dev dev-compose verify up down clean \
        tf-fmt tf-check tf-local-apply tf-local-plan tf-local-destroy \
        app-test app-build app-build-bad app-run smoke \
        cluster-up cluster-down cluster-info \
        k8s-image k8s-apply k8s-delete k8s-smoke k8s-render scan \
        gitea-up gitops-push argocd-install argocd-bootstrap argocd-password argocd-status gitops \
        mon-apply mon-check mon-delete alerts-discord \
        platform-apply platform-delete tls-ca tls-check seal-export seal seal-check \
        aws-whoami tf-bootstrap-apply tf-aws-init tf-aws-plan tf-aws-apply \
        tf-burst-init tf-burst-plan tf-burst-apply tf-burst-destroy \
        burst-kubeconfig burst-alb-controller burst-up burst-argocd-ui burst-down \
        backup restore backup-verify backup-check cost-report metered \
        k6-baseline k6-throttle chaos-1 chaos-2 chaos-3 chaos-4 chaos-5 chaos-all

# Default target. `make` with no arguments should explain itself, never do something.
help:
	@echo "LinkPulse — tasks are defined in mise.toml; this Makefile forwards to mise."
	@echo ""
	@echo "  make dev               bring the whole stack up (k3d included) and prove it works"
	@echo "  make dev-compose       the fast inner loop: API in a container, no cluster"
	@echo "  make verify            unit tests + terraform validate + manifest render"
	@echo ""
	@echo "  make up / down / clean supporting services; clean also drops local state"
	@echo "  make tf-check          fmt check + init + validate, every environment"
	@echo "  make tf-local-apply    apply the local environment against LocalStack"
	@echo "  make app-test          gofmt, vet, unit tests"
	@echo "  make app-build         build linkpulse:local"
	@echo "  make smoke             end-to-end assertions against a running API"
	@echo ""
	@echo "  make cluster-up        create the three-node k3d cluster (idempotent)"
	@echo "  make cluster-down      delete it"
	@echo "  make cluster-info      nodes, pods, ingress, hpa, pdb"
	@echo "  make k8s-image         build the image and load it into the cluster"
	@echo "  make k8s-apply         apply the local overlay and wait for the rollout"
	@echo "  make k8s-smoke         assertions through the Traefik ingress"
	@echo "  make k8s-render        render every kustomization (no cluster needed)"
	@echo "  make scan              Trivy HIGH/CRITICAL gate on linkpulse:local"
	@echo ""
	@echo "  make mon-apply         apply Prometheus/Alertmanager/Grafana/Loki and wait"
	@echo "  make mon-check         prove the stack is observing (targets, rules, render, logs)"
	@echo ""
	@echo "  make platform-apply    cert-manager (the cluster CA) and Sealed Secrets"
	@echo "  make tls-check         every ingress serves a verified certificate from the CA"
	@echo "  make seal-check        the Sealed Secrets round trip, on the live cluster"
	@echo "  make seal-export       back up the sealing key; commit its public certificate"
	@echo ""
	@echo "  make backup-check      export, destroy, recreate, restore, verify -- on LocalStack"
	@echo "  make backup / restore  the same against the real table (AWS credentials in the shell)"
	@echo "  make metered           what is billing by the hour right now"
	@echo "  make cost-report       Cost Explorer by day and service, plus the budgets"
	@echo "  make burst-up / down   the metered window, start and (safely) end"
	@echo ""
	@echo "  make k6-baseline       two minutes of load through the ingress, with thresholds"
	@echo "  make chaos-N           chaos experiment N (1 throttle, 2 bad deploy, 3 outage, 4 OOM, 5 node)"
	@echo "  make chaos-all         all five, in order, after 'make gitops'"
	@echo ""
	@echo "  make gitops            the GitOps path: Gitea + ArgoCD, push, bootstrap, prove Healthy"
	@echo "  make gitops-push       push HEAD to the local Gitea remote"
	@echo "  make argocd-status     every Application with sync and health"
	@echo "  make argocd-password   admin password for http://argocd.localtest.me:8088"
	@echo ""
	@echo "  mise tasks             the full list, straight from mise"

# One rule per task rather than a catch-all pattern rule: an explicit list means `make
# tf-lcoal-apply` fails with "no rule to make target" instead of being forwarded to mise
# and failing with something less obvious.
dev dev-compose verify up down clean tf-fmt tf-check tf-local-apply tf-local-plan tf-local-destroy app-test app-build app-build-bad app-run smoke cluster-up cluster-down cluster-info k8s-image k8s-apply k8s-delete k8s-smoke k8s-render scan gitea-up gitops-push argocd-install argocd-bootstrap argocd-password argocd-status gitops mon-apply mon-check mon-delete alerts-discord platform-apply platform-delete tls-ca tls-check seal-export seal seal-check aws-whoami tf-bootstrap-apply tf-aws-init tf-aws-plan tf-aws-apply tf-burst-init tf-burst-plan tf-burst-apply tf-burst-destroy burst-kubeconfig burst-alb-controller burst-up burst-argocd-ui burst-down backup restore backup-verify backup-check cost-report metered k6-baseline k6-throttle chaos-1 chaos-2 chaos-3 chaos-4 chaos-5 chaos-all:
	@$(MISE) run $@
