#!/usr/bin/env sh
# Empty the burst cluster of everything that owns an AWS resource, BEFORE Terraform
# destroys the cluster. Runs inside the ops container against .kube/burst.
#
# The order matters and is the whole reason this script exists. `terraform destroy` on
# envs/aws-burst deletes what its state file describes: the cluster, the node group, the
# IAM roles. It knows nothing about the ALB, the target groups and the security groups
# the load balancer controller created from the application's Ingress -- those belong to
# the controller, and the controller deletes them when the Ingress goes away. Destroy the
# cluster first and the controller is gone before it can clean up: the ALB is orphaned,
# bills $0.0225 an hour forever, and the only thing that would ever notice is the $1
# budget tripwire, a day later. This is the single most common way an "all destroyed"
# AWS account keeps charging.
#
# So: delete the ArgoCD root Application (its finalizer cascades to every child and every
# resource they deployed, Ingress included), belt-and-braces delete any Ingress that is
# left, then WAIT until the account reports no load balancer tagged with this cluster's
# name. Only then does `mise run burst-down` hand over to `terraform destroy`.
#
# Unverified: no EKS cluster has existed yet. Every command here is one the k3d path has
# exercised or a plain AWS CLI read; the sequence is what is untested.
#
#   scripts/burst-teardown.sh [cluster-name]     # default linkpulse-burst

set -eu

cluster="${1:-linkpulse-burst}"
region="${AWS_REGION:?AWS_REGION must be set}"

say() { printf '==> %s\n' "$*"; }

say "cluster $cluster: current context"
kubectl config current-context

# A backup of the table is the caller's job (mise run burst-down does it first); this
# script only touches the cluster. Stated so nobody adds it here and ends up with a
# teardown that pauses on a Scan of a table it was not asked to read.

say "deleting the ArgoCD root Application (finalizer cascade: projects, applications, everything they deployed)"
kubectl delete application root -n argocd --ignore-not-found --wait --timeout=600s
# Children carry their own finalizers, so waiting on root alone can return while they
# are still tearing down. Wait for the namespace's Applications to be gone outright.
i=0
while [ "$(kubectl get applications -n argocd -o name 2>/dev/null | wc -l)" -gt 0 ]; do
  i=$((i+1)); [ "$i" -gt 120 ] && { echo "FAIL: Applications still present after 10 minutes" >&2; kubectl get applications -n argocd; exit 1; }
  sleep 5
done
echo "    no Applications remain"

say "deleting any Ingress ArgoCD did not own"
kubectl delete ingress --all --all-namespaces --ignore-not-found --wait --timeout=300s

say "waiting for the load balancer controller to release every ALB tagged for this cluster"
# The controller tags everything it creates with elbv2.k8s.aws/cluster=<cluster name>.
# Reading that tag back is the only honest test of "the ALB is gone": the Ingress being
# gone only means the controller has been ASKED.
i=0
while :; do
  arns="$(aws elbv2 describe-load-balancers --region "$region" --query 'LoadBalancers[].LoadBalancerArn' --output text 2>/dev/null || true)"
  remaining=0
  for arn in $arns; do
    if aws elbv2 describe-tags --region "$region" --resource-arns "$arn" \
        --query "TagDescriptions[].Tags[?Key=='elbv2.k8s.aws/cluster' && Value=='$cluster']" --output text | grep -q .; then
      remaining=$((remaining+1))
    fi
  done
  [ "$remaining" -eq 0 ] && break
  i=$((i+1)); [ "$i" -gt 60 ] && { echo "FAIL: $remaining load balancer(s) still tagged for $cluster after 10 minutes; find them in the EC2 console before destroying the cluster" >&2; exit 1; }
  printf '    %d load balancer(s) still present\n' "$remaining"
  sleep 10
done
echo "    no load balancers tagged elbv2.k8s.aws/cluster=$cluster"

say "uninstalling the load balancer controller (its webhook would otherwise block the namespace deletions Terraform triggers)"
helm uninstall aws-load-balancer-controller -n kube-system --ignore-not-found --wait --timeout 5m || true

say "cluster emptied; safe to: mise run tf-burst-destroy"
