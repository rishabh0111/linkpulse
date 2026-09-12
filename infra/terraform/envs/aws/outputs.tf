output "table_name" {
  description = "For the application's DDB_TABLE."
  value       = module.table.table_name
}

output "table_arn" {
  description = "Table ARN."
  value       = module.table.table_arn
}

output "provisioned_write_capacity" {
  description = "The WCU ceiling chaos experiment 1 drives k6 past. Asserted by the experiment so it cannot silently drift."
  value       = module.table.provisioned_write_capacity
}

output "app_policy_arn" {
  description = "Least-privilege policy ARN. Chaos experiment 3 detaches exactly this."
  value       = module.iam.policy_arn
}

output "app_user_name" {
  description = "IAM user for the local cluster. Create its access key out of band."
  value       = module.iam.app_user_name
}

output "vpc_id" {
  description = "VPC ID, consumed by envs/aws-burst through remote state."
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnet IDs."
  value       = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet IDs."
  value       = module.vpc.private_subnet_ids
}

output "billed_resources" {
  description = "Metered resources in this environment. An empty list is the correct answer here, always."
  value       = module.vpc.billed_resources
}
