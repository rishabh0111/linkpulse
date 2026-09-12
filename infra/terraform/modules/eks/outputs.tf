output "cluster_name" {
  description = "Cluster name, for `aws eks update-kubeconfig`."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_version" {
  description = "Running control plane version."
  value       = aws_eks_cluster.this.version
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN. Feed this to the iam module to create the IRSA role."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_provider_url" {
  description = "OIDC issuer URL with the scheme stripped, which is the form IAM condition keys need."
  value       = replace(aws_eks_cluster.this.identity[0].oidc[0].issuer, "https://", "")
}

output "node_group_name" {
  description = "Managed node group name."
  value       = aws_eks_node_group.this.node_group_name
}

output "node_subnet_tier" {
  description = "Which tier the nodes actually landed in, echoed so the burst runbook can record it."
  value       = var.node_subnet_tier
}

output "update_kubeconfig_command" {
  description = "The exact command to get a kubeconfig for this cluster. Emitted so the burst runbook does not have to reconstruct it under time pressure."
  value       = "aws eks update-kubeconfig --region ${data.aws_region.current.region} --name ${aws_eks_cluster.this.name}"
}

output "estimated_hourly_usd" {
  description = <<-EOT
    Rough hourly burn for what this module created, control plane plus nodes plus disk.
    Excludes the ALB and data transfer. Emitted so `terraform output` states the cost of
    leaving the cluster up — the number that matters most inside a metered window.
  EOT
  value       = format("%.4f", 0.10 + (var.node_count * 0.0224) + (var.node_count * var.disk_size * 0.08 / 730))
}

data "aws_region" "current" {}
