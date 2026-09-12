output "policy_arn" {
  description = <<-EOT
    ARN of the table-access policy.

    Chaos experiment 3 (dependency outage) detaches exactly this policy and reattaches it,
    which is why it is an output rather than an internal detail — the experiment script
    takes it from `terraform output` instead of hardcoding an ARN that would silently go
    stale the next time the environment is rebuilt.
  EOT
  value       = aws_iam_policy.table_access.arn
}

output "policy_name" {
  description = "Policy name, for readable output in the incident timeline."
  value       = aws_iam_policy.table_access.name
}

output "irsa_role_arn" {
  description = "IRSA role ARN, annotated onto the service account during the EKS burst. Null outside it."
  value       = try(aws_iam_role.irsa[0].arn, null)
}

output "irsa_role_name" {
  description = "IRSA role name — the detach target for experiment 3 when running on EKS."
  value       = try(aws_iam_role.irsa[0].name, null)
}

output "app_user_name" {
  description = "IAM user for the local cluster. Create its access key out of band: aws iam create-access-key --user-name <this>."
  value       = try(aws_iam_user.app[0].name, null)
}
