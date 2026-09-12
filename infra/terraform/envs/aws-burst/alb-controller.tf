# IRSA role for the AWS Load Balancer Controller (platform/aws-load-balancer-controller).
#
# The aws overlay's Ingress says `ingressClassName: alb`, and nothing in EKS acts on that
# by itself: the controller is a workload that has to be installed and needs permission
# to create load balancers, target groups, listeners and security groups on the account's
# behalf. Without this file the burst cluster would come up, ArgoCD would report the
# application Healthy, and no address would ever appear on the Ingress -- a failure that
# looks like a networking problem and is a missing controller.
#
# The policy is the controller project's own, vendored byte-for-byte at the version the
# chart is pinned to (alb-controller-iam-policy-v3.5.0.json). It is broad by necessity --
# ELB and EC2 APIs are largely not resource-scoped -- and it is the second-widest grant
# in the account after the operator's own credentials. That is why it is a role only the
# controller's ServiceAccount can assume, and why it lives in the metered environment:
# it is destroyed with the cluster.
#
# Same three-part pattern as cloudwatch.tf: a trust policy pinned to one ServiceAccount
# by namespace and name, a permissions policy, a role joining them.

data "aws_iam_policy_document" "alb_controller_trust" {
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
      values   = ["system:serviceaccount:kube-system:aws-load-balancer-controller"]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "alb_controller" {
  name        = "${local.name}-burst-alb-controller"
  description = "AWS Load Balancer Controller v3.5.0 permissions, vendored from the upstream iam_policy.json."
  policy      = file("${path.module}/alb-controller-iam-policy-v3.5.0.json")

  tags = local.tags
}

resource "aws_iam_role" "alb_controller" {
  name               = "${local.name}-burst-alb-controller"
  description        = "Assumed by the AWS Load Balancer Controller to create the ALB behind the application's Ingress."
  assume_role_policy = data.aws_iam_policy_document.alb_controller_trust.json

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "alb_controller" {
  role       = aws_iam_role.alb_controller.name
  policy_arn = aws_iam_policy.alb_controller.arn
}

output "alb_controller_role_arn" {
  description = "Substitute into platform/aws-load-balancer-controller/values.yaml (serviceAccount.annotations) before `mise run burst-alb-controller`."
  value       = aws_iam_role.alb_controller.arn
}

output "alb_controller_helm_args" {
  description = "The --set flags the Helm install needs from this environment, ready to paste."
  value       = "--set clusterName=${module.eks.cluster_name} --set region=${var.region} --set vpcId=${data.terraform_remote_state.base.outputs.vpc_id} --set serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=${aws_iam_role.alb_controller.arn}"
}
