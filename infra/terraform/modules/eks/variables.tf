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
  description = "Node instance types. t3.small is the smallest that runs the monitoring stack alongside the app without evictions."
  type        = list(string)
  default     = ["t3.small"]
}

variable "node_count" {
  description = "Desired node count. Two is the floor for a meaningful drain or node-loss demonstration."
  type        = number
  default     = 2

  validation {
    condition     = var.node_count >= 2 && var.node_count <= 3
    error_message = "node_count must be 2-3: fewer makes the PDB demo meaningless, more spends the burst budget on idle capacity."
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
