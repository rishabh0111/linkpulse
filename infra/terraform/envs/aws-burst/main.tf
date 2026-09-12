# envs/aws-burst — the metered environment. This is the only state file that costs money.
#
# It exists for one 72-hour window, budgeted at $25, and then `terraform destroy` ends it.
# It is a separate state file from envs/aws so that destroy cannot touch the DynamoDB
# table, the IAM policy or the VPC: the most dangerous command in the project runs against
# a state file that contains nothing irreplaceable.
#
# The runbook for this window is written BEFORE the clock starts (docs/burst-runbook.md).
# Metered time is for executing decisions, not making them.

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

locals {
  name = "linkpulse"

  tags = {
    Project     = "linkpulse"
    Environment = "aws-burst"
    ManagedBy   = "terraform"
    Metered     = "true" # so Cost Explorer can group by exactly this
    Repo        = var.repo_url
  }
}

# The always-free environment's outputs, read rather than duplicated. Hardcoding subnet
# IDs here would work right up until the VPC was rebuilt, and then fail with an error
# about a subnet that does not exist rather than about stale configuration.
data "terraform_remote_state" "base" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "aws/terraform.tfstate"
    region = var.region
  }
}

# ---------------------------------------------------------------------------------------
# The cluster
# ---------------------------------------------------------------------------------------

module "eks" {
  source = "../../modules/eks"

  cluster_name       = "${local.name}-burst"
  kubernetes_version = var.kubernetes_version

  public_subnet_ids  = data.terraform_remote_state.base.outputs.public_subnet_ids
  private_subnet_ids = data.terraform_remote_state.base.outputs.private_subnet_ids

  # Public tier: avoids a NAT Gateway, which would cost more over the window than the
  # public addresses do. The reasoning is in the module and in docs/architecture.md.
  node_subnet_tier = "public"

  tags = local.tags
}

# ---------------------------------------------------------------------------------------
# IRSA
#
# The same iam module as envs/aws, called a second time — this time with the cluster's
# OIDC provider, which is what turns it into a role a pod can assume. That the module
# serves both cases from one policy document is the point: the permissions the pod gets
# on EKS are byte-for-byte the ones the local cluster gets, so "it worked locally" means
# something.
#
# Note this creates a SECOND policy (linkpulse-burst-table-access) rather than attaching
# the one from envs/aws. Reaching across state files to attach a policy would couple the
# two environments and break the destroy-safety property this split exists for. Two
# identical policies generated from one source is the cheaper trade.
# ---------------------------------------------------------------------------------------

module "irsa" {
  source = "../../modules/iam"

  name_prefix = "${local.name}-burst"
  table_arn   = data.terraform_remote_state.base.outputs.table_arn
  index_arns  = ["${data.terraform_remote_state.base.outputs.table_arn}/index/*"]

  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url

  service_account_namespace = var.service_account_namespace
  service_account_name      = var.service_account_name

  # No static user here. That is the entire IRSA argument: on EKS there is no long-lived
  # credential to store, rotate or leak.
  create_app_user = false

  tags = local.tags
}
