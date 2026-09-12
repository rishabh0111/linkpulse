# IRSA role for the CloudWatch exporter (monitoring/overlays/aws/cloudwatch-exporter.yaml).
#
# A second, separate role rather than a broader application role. The application's
# role grants DynamoDB item access and nothing else; this one grants CloudWatch reads and
# nothing else. Each pod assumes exactly the role for the work it does, which is the
# whole reason IRSA is worth the trust-policy fiddliness over a node-level instance role
# that every pod on the node would share.
#
# Not in modules/iam: that module is the table-access policy, and bolting an unrelated
# statement onto it would make "least privilege for the API" a claim with an asterisk.

data "aws_iam_policy_document" "cloudwatch_exporter_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:monitoring:cloudwatch-exporter"]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "cloudwatch_exporter" {
  # CloudWatch metric APIs are not resource-scoped; "*" is the only valid resource here.
  # The tagging API is what lets the exporter find the table by its Project tag rather
  # than by a hardcoded name.
  statement {
    sid    = "ReadMetrics"
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "tag:GetResources",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "cloudwatch_exporter" {
  name               = "${local.name}-burst-cloudwatch-exporter"
  description        = "Assumed by the CloudWatch exporter pod to read DynamoDB metrics for Prometheus."
  assume_role_policy = data.aws_iam_policy_document.cloudwatch_exporter_trust.json

  tags = local.tags
}

resource "aws_iam_role_policy" "cloudwatch_exporter" {
  name   = "read-metrics"
  role   = aws_iam_role.cloudwatch_exporter.id
  policy = data.aws_iam_policy_document.cloudwatch_exporter.json
}

output "cloudwatch_exporter_role_arn" {
  description = "Annotate monitoring/overlays/aws's cloudwatch-exporter ServiceAccount with this."
  value       = aws_iam_role.cloudwatch_exporter.arn
}
