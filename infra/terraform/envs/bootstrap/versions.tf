terraform {
  required_version = ">= 1.13"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Pinned exactly, and the lock file is committed. This is where reproducibility is
      # actually decided; the modules only constrain loosely.
      #
      # This version has a hard floor on the LocalStack side: AWS provider 6.13 and later
      # cannot create an aws_dynamodb_table against LocalStack 4.9 or older. CreateTable
      # succeeds, then the provider polls DescribeTable, treats the response as a missing
      # resource, and fails with "waiting for update ... couldn't find resource (21
      # retries)" on a table that is sitting there ACTIVE. LocalStack 4.14 fixes it.
      #
      # So docker-compose.yml pins localstack to 4.14, and that pin is not cosmetic —
      # dropping it back would break `terraform apply` in CI while leaving real AWS fine,
      # which is the worst shape of failure to debug. Bisected: provider 6.12 was the last
      # version that worked against 4.9.
      version = "~> 6.64"
    }
  }
}
