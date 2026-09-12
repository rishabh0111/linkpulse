# Bootstrap: creates the S3 bucket that every other environment stores its state in.
#
# This environment keeps its state LOCAL, and that is not an oversight. The bucket cannot
# hold the state that describes the bucket before the bucket exists — the standard
# chicken-and-egg of a remote backend. The two ways out are to create it by hand (and have
# an untracked resource in the account) or to create it here with local state (and have one
# small state file that lives outside S3). The second is better: the bucket is still
# described by code, still tagged, and still destroyable by Terraform.
#
# Run once, then commit nothing but the code — terraform.tfstate for this directory is
# gitignored, since it is the one state file with nowhere else to live.
#
#   terraform -chdir=infra/terraform/envs/bootstrap init
#   terraform -chdir=infra/terraform/envs/bootstrap apply
#   terraform -chdir=infra/terraform/envs/bootstrap output backend_config

terraform {
  # Explicitly local. Stated rather than defaulted, so the next reader knows it was a
  # decision.
  backend "local" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

locals {
  tags = {
    Project     = "linkpulse"
    Environment = "bootstrap"
    ManagedBy   = "terraform"
    Component   = "tfstate"
  }
}

module "state" {
  source = "../../modules/s3-state"

  # S3 bucket names are globally unique across all of AWS, so a fixed name would collide
  # with whoever already took it. The account ID is the conventional disambiguator and is
  # not sensitive.
  bucket_name = "linkpulse-tfstate-${data.aws_caller_identity.current.account_id}"

  tags = local.tags
}

data "aws_caller_identity" "current" {}
