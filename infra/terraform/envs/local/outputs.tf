output "table_name" {
  description = "For the application's DDB_TABLE."
  value       = module.table.table_name
}

output "table_arn" {
  description = "Table ARN as LocalStack reports it."
  value       = module.table.table_arn
}

output "provisioned_write_capacity" {
  description = "Must match envs/aws, or the chaos thresholds calibrated in CI do not transfer."
  value       = module.table.provisioned_write_capacity
}

output "app_policy_arn" {
  description = "Least-privilege policy. Chaos experiment 3 detaches this when running locally."
  value       = module.iam.policy_arn
}

output "app_user_name" {
  description = "IAM user the local app would use, if LocalStack enforced authorization."
  value       = module.iam.app_user_name
}
