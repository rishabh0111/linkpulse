output "state_bucket" {
  description = "Bucket name to paste into the other environments' backend blocks."
  value       = module.state.bucket_name
}

output "backend_config" {
  description = "The exact backend block for the other environments, with the bucket and region filled in."
  value       = module.state.backend_config
}
