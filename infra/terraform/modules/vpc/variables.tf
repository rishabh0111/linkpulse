variable "name" {
  description = "Name prefix for network resources."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR. A /16 leaves room for the /20 subnets the VPC CNI needs."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrsubnet(var.vpc_cidr, 4, 3))
    error_message = "vpc_cidr must be a CIDR block with room for at least four /20 subnets (a /16 or /18)."
  }
}

variable "enable_nat_gateway" {
  description = <<-EOT
    Create a NAT Gateway. Off by default: ~$0.045/hr plus $0.045/GB, billed from creation
    to deletion whether or not anything routes through it. Only needed for outbound
    internet from the private tier — the DynamoDB gateway endpoint already covers the
    application's data path for free.
  EOT
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to every network resource."
  type        = map(string)
  default     = {}
}
