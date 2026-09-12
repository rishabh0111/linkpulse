# envs/local — the same modules, pointed at LocalStack.
#
# This environment is what keeps the repository runnable after the AWS account closes, and
# it is what lets `terraform apply` run in CI on every push without touching a real
# account. The modules are byte-for-byte the ones envs/aws uses; only the provider's
# endpoints differ. That is the whole claim: if the modules only worked against a mock,
# they would prove nothing about the real thing, and vice versa.
#
# What is deliberately NOT here:
#
#   - the vpc and eks modules. LocalStack community does not implement EKS, and its EC2
#     networking emulation is partial. A CI apply that fails on an unimplemented API
#     teaches nothing and trains everyone to ignore a red build.
#   - the budget guardrails. AWS Budgets is not in LocalStack community either, and a
#     cost tripwire is meaningless against a mock.
#
# Naming the exclusions is more honest than quietly having a "local" environment that is
# a different shape from the real one.

provider "aws" {
  region = var.region

  # LocalStack accepts any credentials. These are literal placeholders, not secrets, and
  # they are hardcoded rather than read from the environment so that a real credential
  # sitting in the shell can never be picked up and used against a real account by an
  # environment named "local".
  access_key = "test"
  secret_key = "test"

  # LocalStack does not implement the credential and metadata endpoints these checks use,
  # and without skipping them the provider spends its startup budget timing out.
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_region_validation      = true

  # Virtual-host-style addressing needs per-bucket DNS, which a single container on
  # localhost does not have. Path style puts the bucket in the URL path instead.
  s3_use_path_style = true

  # Every service the configuration touches must be listed. An unlisted service is not an
  # error — the SDK quietly uses the real AWS endpoint for it, which is how the first
  # apply here ended up sending an S3 Control ListTagsForResource to actual AWS and
  # getting an AccessDenied back from Amazon. It failed safe (the credentials are
  # "test"), but silently talking to a real endpoint from an environment named "local" is
  # not a thing to leave in place.
  #
  # s3control is separate from s3: the provider reads bucket tags through the S3 Control
  # API, so overriding s3 alone does not cover the tag read.
  endpoints {
    dynamodb  = var.localstack_endpoint
    s3        = var.localstack_endpoint
    s3control = var.localstack_endpoint
    iam       = var.localstack_endpoint
    sts       = var.localstack_endpoint
  }
}

locals {
  name = "linkpulse"

  tags = {
    Project     = "linkpulse"
    Environment = "local"
    ManagedBy   = "terraform"
  }
}

module "table" {
  source = "../../modules/dynamodb-table"

  table_name = local.name

  # The same 20/20 as the real environment, and that matters more than it looks: chaos
  # experiments 2 through 5 run against LocalStack in CI, so the capacity the CI table is
  # provisioned at has to match the capacity the assertions were calibrated against.
  read_capacity  = 20
  write_capacity = 20

  tags = local.tags
}

module "iam" {
  source = "../../modules/iam"

  name_prefix = "${local.name}-local"
  table_arn   = module.table.table_arn
  index_arns  = module.table.index_arns

  create_app_user   = true
  oidc_provider_arn = null

  tags = local.tags
}

# Applied here purely so CI exercises the module. Nothing uses this bucket as a backend —
# this environment keeps local state (see versions.tf) — but a module that is only ever
# applied against a real account during a one-off bootstrap is a module that is never
# tested. Running it here means a syntax or ordering error in the state module surfaces on
# a push rather than on the day the account is set up.
module "state" {
  source = "../../modules/s3-state"

  bucket_name = "${local.name}-tfstate-local"
  tags        = local.tags
}
