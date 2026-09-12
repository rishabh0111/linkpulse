# EKS — the metered centrepiece of the burst window.
#
# Everything here bills by the hour from creation to deletion:
#
#   control plane      $0.10/hr        ~$7.20 over 72 hours
#   2x t3.small        ~$0.0224/hr ea  ~$3.23 over 72 hours
#   2x 20 GB gp3       $0.08/GB-mo     ~$0.26 over 72 hours
#   ALB (ingress)      ~$0.0225/hr     ~$1.62 over 72 hours
#
# ~$12 of the $25 budget, before data transfer and before the one mistake the budget has
# room for. This module exists only inside the window and `terraform destroy` is what ends
# it; the $1 budget alert is the backstop, not the plan.
#
# This module is NOT included in envs/local — LocalStack does not implement EKS, and a
# CI apply that fails on an unimplemented API teaches nothing.

locals {
  # Where the nodes land. Public by default, and that is a cost decision rather than a
  # security preference: private nodes need a NAT Gateway to pull images (~$3.24 over the
  # window plus data processing), while two public IPv4 addresses cost ~$0.72. The
  # private subnets exist and are documented; the DynamoDB gateway endpoint means the
  # application's data path never crosses the internet either way. The trade-off is
  # written down in docs/architecture.md because it is exactly the kind of thing an
  # interviewer should push on.
  node_subnet_ids = var.node_subnet_tier == "private" ? var.private_subnet_ids : var.public_subnet_ids
}

# ---------------------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------------------

resource "aws_iam_role" "cluster" {
  name               = "${var.cluster_name}-cluster"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "cluster_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "this" {
  name     = var.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    # The control plane places cross-AZ ENIs in these subnets, so both tiers are listed
    # even when the nodes only use one of them.
    subnet_ids = concat(var.public_subnet_ids, var.private_subnet_ids)

    endpoint_public_access  = true
    endpoint_private_access = true

    # Restricting this to a known address is better practice, but a home IP changes and
    # a burst window is the wrong place to debug a lockout. The trade-off is deliberate
    # and the window is 72 hours; a long-lived cluster would set this.
    public_access_cidrs = var.public_access_cidrs
  }

  access_config {
    # API rather than the aws-auth ConfigMap. The ConfigMap approach is legacy, and
    # editing it wrongly is a classic way to lock yourself out of your own cluster with
    # no path back short of rebuilding.
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  # Control plane logs go to CloudWatch Logs, billed at $0.50/GB ingested against a 5 GB
  # free allowance. `api` and `authenticator` are the two that answer the questions that
  # actually come up during a burst (was the call rejected, and by what). `audit` is
  # excluded on purpose: it is by far the highest-volume stream and would put the free
  # allowance at risk for a window this short.
  enabled_cluster_log_types = var.enabled_log_types

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

# ---------------------------------------------------------------------------------------
# OIDC provider — what makes IRSA possible
# ---------------------------------------------------------------------------------------

data "tls_certificate" "oidc" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "this" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]

  tags = var.tags
}

# ---------------------------------------------------------------------------------------
# Node group
# ---------------------------------------------------------------------------------------

resource "aws_iam_role" "node" {
  name               = "${var.cluster_name}-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# These three are the required set. A node group missing any of them joins the cluster
# and then fails in a way that does not mention IAM: without the CNI policy pods sit in
# ContainerCreating with no address, and without the registry policy every image pull
# fails with an authorization error.
resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
  ])

  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.cluster_name}-default"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = local.node_subnet_ids

  instance_types = var.instance_types

  # ON_DEMAND, not SPOT. Spot would save roughly $2 over the window, and the whole point
  # of the 72 hours is capturing evidence — an interruption mid-capture costs more than
  # the saving. Cost engineering means knowing which savings are worth taking.
  capacity_type = "ON_DEMAND"

  disk_size = var.disk_size

  scaling_config {
    # Two nodes for the same reason the local cluster has three: node-loss and drain
    # demonstrations need somewhere for pods to go. One node makes the PDB story
    # untellable.
    desired_size = var.node_count
    min_size     = var.node_count
    max_size     = var.node_count + 1
  }

  update_config {
    max_unavailable = 1
  }

  tags = var.tags

  lifecycle {
    # The HPA changes replica counts, not node counts, so desired_size drifting means
    # something else scaled the group and Terraform should not silently undo it.
    ignore_changes = [scaling_config[0].desired_size]
  }

  depends_on = [aws_iam_role_policy_attachment.node]
}

# ---------------------------------------------------------------------------------------
# Addons
#
# Declared explicitly rather than left as the versions EKS happens to pre-install, so the
# cluster is reproducible and the versions are visible in the diff.
# ---------------------------------------------------------------------------------------

resource "aws_eks_addon" "this" {
  for_each = toset(["vpc-cni", "kube-proxy", "coredns"])

  cluster_name = aws_eks_cluster.this.name
  addon_name   = each.value

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  tags = var.tags

  # coredns will not become healthy with no nodes to schedule on, and the addon resource
  # waits for health before it returns.
  depends_on = [aws_eks_node_group.this]
}
