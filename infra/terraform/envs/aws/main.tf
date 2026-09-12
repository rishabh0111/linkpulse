# envs/aws — the always-free environment.
#
# Everything in this state file costs nothing per hour and runs for the whole credit
# window: the DynamoDB table (table plus index provisioned to exactly the 25/25 allowance), the IAM policy, the
# VPC and everything in it except NAT, and the budget guardrail.
#
# The metered resources live in envs/aws-burst, in a separate state file, on purpose. That
# is not tidiness — it is a safety property. `terraform destroy` here and `terraform
# destroy` there cannot reach each other's resources, so ending the burst window is a
# destroy that is *incapable* of deleting the table holding the data. Splitting the state
# is what makes the most dangerous command in the project safe to run in a hurry.

provider "aws" {
  region = var.region

  # Applied to every taggable resource in this environment, so cost allocation works
  # without remembering to tag anything by hand. Consistent tagging from the first
  # commit, as the plan requires.
  default_tags {
    tags = local.tags
  }
}

locals {
  name = "linkpulse"

  tags = {
    Project     = "linkpulse"
    Environment = "aws"
    ManagedBy   = "terraform"
    Repo        = var.repo_url
  }
}

# ---------------------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------------------

module "table" {
  source = "../../modules/dynamodb-table"

  table_name = local.name

  # Left at the module defaults (20/20 on the table, 5/5 on the index: 25/25 in total)
  # deliberately. The module's variable validation refuses any combination above 25, so
  # the free-tier ceiling is enforced at plan time rather than discovered on a bill.
  tags = local.tags
}

# ---------------------------------------------------------------------------------------
# Least-privilege access
# ---------------------------------------------------------------------------------------

module "iam" {
  source = "../../modules/iam"

  name_prefix = "${local.name}-aws"
  table_arn   = module.table.table_arn
  index_arns  = module.table.index_arns

  # The local k3d cluster runs against this real table, and it has no OIDC provider to
  # federate with, so it needs a user. The access key is created out of band — see the
  # module's main.tf for why it is not a Terraform resource.
  create_app_user = true

  # No OIDC provider in this environment: IRSA belongs to the burst, which has the
  # cluster. Passing null is what makes the same module serve both.
  oidc_provider_arn = null

  tags = local.tags
}

# ---------------------------------------------------------------------------------------
# The network
#
# Built early and kept for the whole window. Free, and it closes the cloud-networking gap
# the plan identifies without spending a cent.
# ---------------------------------------------------------------------------------------

module "vpc" {
  source = "../../modules/vpc"

  name     = local.name
  vpc_cidr = var.vpc_cidr

  # The one metered thing this module can create, and it stays off. The burst runs its
  # nodes in the public tier instead; the DynamoDB gateway endpoint means the
  # application's data path never needs it either way.
  enable_nat_gateway = false

  tags = local.tags
}
