output "bucket_name" {
  description = "Bucket to put in each environment's backend block."
  value       = aws_s3_bucket.state.id
}

output "bucket_arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.state.arn
}

output "backend_config" {
  description = <<-EOT
    A ready-to-paste backend block for the environments. Emitted as an output so the
    bootstrap step hands over the exact values rather than leaving them to be retyped —
    a mistyped key is how two environments end up sharing one state file.
  EOT
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${aws_s3_bucket.state.id}"
        key          = "<env>/terraform.tfstate"
        region       = "${data.aws_region.current.region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  EOT
}

data "aws_region" "current" {}
