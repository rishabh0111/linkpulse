output "cluster_name" {
  description = "Cluster name."
  value       = module.eks.cluster_name
}

output "update_kubeconfig_command" {
  description = "Paste-ready kubeconfig command, so the burst runbook does not reconstruct it under a clock."
  value       = module.eks.update_kubeconfig_command
}

output "irsa_role_arn" {
  description = <<-EOT
    Role ARN for the service account annotation:

      eks.amazonaws.com/role-arn: <this>

    Applied to k8s/manifests/serviceaccount.yaml during the burst. A missing or mistyped
    annotation is the failure that looks like a credentials problem inside the pod and
    like nothing at all from the Terraform side.
  EOT
  value       = module.irsa.irsa_role_arn
}

output "irsa_role_name" {
  description = "Role name — the detach target for chaos experiment 3 when it runs against EKS."
  value       = module.irsa.irsa_role_name
}

output "app_policy_arn" {
  description = "The burst copy of the least-privilege policy."
  value       = module.irsa.policy_arn
}

output "estimated_hourly_usd" {
  description = "Control plane + nodes + disk, per hour. Excludes the ALB and data transfer."
  value       = module.eks.estimated_hourly_usd
}

output "estimated_72h_usd" {
  description = "The number that decides whether the window is affordable. Compare against burst_budget_usd in envs/aws."
  value       = format("%.2f", tonumber(module.eks.estimated_hourly_usd) * 72)
}
