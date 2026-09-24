variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
}

variable "kubernetes_version" {
  description = "Control plane minor version. Pinned so the cluster does not come up on a different version than the local k3d one."
  type        = string
  default     = "1.34"
}

variable "public_subnet_ids" {
  description = "Public subnet IDs from the vpc module."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs from the vpc module."
  type        = list(string)
}

variable "node_subnet_tier" {
  description = <<-EOT
    Which tier the managed nodes run in. "public" avoids a NAT Gateway (~$3.24 over a
    72-hour window plus data processing) at the cost of ~$0.72 in public IPv4 charges;
    "private" is the more conventional layout and requires enable_nat_gateway on the vpc
    module. See the local in main.tf.
  EOT
  type        = string
  default     = "public"

  validation {
    condition     = contains(["public", "private"], var.node_subnet_tier)
    error_message = "node_subnet_tier must be \"public\" or \"private\"."
  }
}

variable "instance_types" {
  # Sized by the VPC CNI's per-node pod cap (ENIs x (IPs per ENI - 1) + 2), not by CPU or
  # memory. And constrained by the account: on an AWS Free *plan* account only Free Tier
  # eligible types launch at all -- t3.medium was tried and the node group failed with
  # "The specified instance type is not eligible for Free Tier", after the old group had
  # already been destroyed. The eligible list and what it buys per node:
  #
  #   t3.small         11 pods   2 GB   $0.0224/hr
  #   c7i-flex.large   29 pods   4 GB   $0.0848/hr
  #   m7i-flex.large   29 pods   8 GB   $0.1008/hr
  #
  # Four t3.small (44 pods, $0.0896/hr) beats two c7i-flex.large (58 pods, $0.1696/hr) for
  # this workload: it needs pod slots, not cores. Check eligibility before changing this:
  #   aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true
  description = "Node instance types. See the note above: the pod cap and the account's Free Tier eligibility both bind."
  type        = list(string)
  default     = ["t3.small"]
}

variable "node_count" {
  # Two t3.small nodes was the original sizing and it could not run this project. The
  # binding limit was never CPU or memory -- both nodes sat at half their memory -- but
  # the AWS VPC CNI's ENI-derived pod cap: 11 pods per t3.small, 22 in total, against ~30
  # pods of system, ArgoCD, platform, app and monitoring workloads, plus headroom for the
  # HPA during the chaos experiments. Eight monitoring pods stayed Pending with "Too many
  # pods". Found on the burst cluster; k3d has no such cap, so nothing local could show
  # it. Four t3.small gives 44 slots for about $2 a day.
  description = "Desired node count. Two is the floor for a meaningful drain or node-loss demonstration."
  type        = number
  default     = 4

  validation {
    condition     = var.node_count >= 2 && var.node_count <= 4
    error_message = "node_count must be 2-4: fewer makes the PDB demo meaningless, more spends the burst budget on idle capacity."
  }
}

variable "disk_size" {
  description = "Node EBS volume size in GB, billed at $0.08/GB-month."
  type        = number
  default     = 20
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public API endpoint."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enabled_log_types" {
  description = "Control plane log types shipped to CloudWatch Logs. `audit` is excluded by default; see main.tf."
  type        = list(string)
  default     = ["api", "authenticator"]
}

variable "tags" {
  description = "Tags applied to every EKS resource."
  type        = map(string)
  default     = {}
}
