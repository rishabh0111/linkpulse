# Least-privilege access to the single table.
#
# The permission set is derived from the application, not guessed: every action below maps
# to a call in app/graphql-api/internal/store/dynamo.go, and nothing else is granted. The
# access patterns in docs/data-model.md are the specification for this policy.
#
#   GetItem        A1  resolve a code
#   PutItem        A2  create a link (conditional), A3 write a CLICK item
#   UpdateItem     A3  ADD 1 to the CLICKSTAT aggregate
#   Query          A4/A5/A6 on the table, A7 on GSI1
#   DescribeTable      the /readyz probe
#
# Notably absent: Scan, DeleteItem, BatchWriteItem, and every control-plane action. The
# application makes none of those calls, so granting them would be granting blast radius
# for nothing.

locals {
  # Item-level operations address the table itself. An index cannot be the target of a
  # GetItem or a write, so listing the index ARN here would be permission that can never
  # be exercised.
  item_actions = [
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
  ]

  # Query is the one action that spans both: A4/A5/A6 query the table, A7 queries GSI1.
  # An index is a distinct resource ARN, so a policy covering only the table ARN makes A7
  # fail with AccessDenied at runtime and nowhere earlier.
  query_resources = concat([var.table_arn], var.index_arns)
}

data "aws_iam_policy_document" "table_access" {
  statement {
    sid       = "ItemLevelAccess"
    effect    = "Allow"
    actions   = local.item_actions
    resources = [var.table_arn]
  }

  statement {
    sid       = "QueryTableAndIndexes"
    effect    = "Allow"
    actions   = ["dynamodb:Query"]
    resources = local.query_resources
  }

  statement {
    sid       = "ReadinessProbe"
    effect    = "Allow"
    actions   = ["dynamodb:DescribeTable"]
    resources = [var.table_arn]
  }

  # Defense in depth, not redundancy. These are already denied implicitly, but an explicit
  # Deny cannot be overridden by any later Allow from another attached policy — so a Scan
  # can never be introduced by accident. Scan specifically: on this table a single Scan
  # reads every click item ever written and would exhaust the 25 RCU allowance in one call.
  statement {
    sid    = "DenyExpensiveAndDestructive"
    effect = "Deny"

    actions = [
      "dynamodb:Scan",
      "dynamodb:DeleteItem",
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:CreateTable",
    ]

    resources = [
      var.table_arn,
      "${var.table_arn}/index/*",
    ]
  }
}

resource "aws_iam_policy" "table_access" {
  name        = "${var.name_prefix}-table-access"
  description = "Least-privilege DynamoDB access for the LinkPulse API. Derived from the access patterns in docs/data-model.md."
  policy      = data.aws_iam_policy_document.table_access.json

  tags = var.tags
}

# ---------------------------------------------------------------------------------------
# IRSA — used during the EKS burst.
#
# The pod assumes this role through the cluster's OIDC provider, so no credential is ever
# stored in the cluster. Created only when an OIDC provider ARN is supplied, which is why
# the local and LocalStack environments can use this same module without it.
# ---------------------------------------------------------------------------------------

data "aws_iam_policy_document" "irsa_trust" {
  count = var.oidc_provider_arn == null ? 0 : 1

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    # Both conditions are required, and the `sub` one is the whole point. With only the
    # audience check, *any* service account in the cluster could assume this role — which
    # is the subtly-wrong trust policy the plan calls out as a burst-window time sink.
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.service_account_namespace}:${var.service_account_name}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "irsa" {
  count = var.oidc_provider_arn == null ? 0 : 1

  name               = "${var.name_prefix}-irsa"
  description        = "Assumed by the LinkPulse pod via the EKS OIDC provider."
  assume_role_policy = data.aws_iam_policy_document.irsa_trust[0].json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "irsa" {
  count = var.oidc_provider_arn == null ? 0 : 1

  role       = aws_iam_role.irsa[0].name
  policy_arn = aws_iam_policy.table_access.arn
}

# ---------------------------------------------------------------------------------------
# Static credentials — used by the local k3d cluster against the real table.
#
# The user is managed here; the access key deliberately is NOT. An
# aws_iam_access_key resource writes the secret into Terraform state in plaintext, and
# state is a file that gets copied, versioned and occasionally pasted. The key is created
# out of band instead:
#
#   aws iam create-access-key --user-name <output.app_user_name>
#
# and lands in the cluster as a Sealed Secret. That keeps the one genuinely
# unrotatable-by-Terraform secret out of the one file guaranteed to be replicated.
# ---------------------------------------------------------------------------------------

resource "aws_iam_user" "app" {
  count = var.create_app_user ? 1 : 0

  name = "${var.name_prefix}-app"
  tags = var.tags
}

resource "aws_iam_user_policy_attachment" "app" {
  count = var.create_app_user ? 1 : 0

  user       = aws_iam_user.app[0].name
  policy_arn = aws_iam_policy.table_access.arn
}
