variable "region" {
  description = "Region LocalStack pretends to be. Matches envs/aws so ARNs have the same shape."
  type        = string
  default     = "ap-south-1"
}

variable "localstack_endpoint" {
  description = <<-EOT
    Where LocalStack is reachable.

    localhost from the host; http://localstack:4566 from inside the compose network,
    which is how the CI job and `mise run dev` reach it. Passed as TF_VAR_localstack_endpoint
    rather than edited, so the same tfvars works in both places.
  EOT
  type        = string
  default     = "http://localhost:4566"
}
