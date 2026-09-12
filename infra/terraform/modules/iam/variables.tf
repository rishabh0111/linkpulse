variable "name_prefix" {
  description = "Prefix for IAM resource names, e.g. linkpulse-aws."
  type        = string
}

variable "table_arn" {
  description = "ARN of the table this policy grants access to. Comes from the dynamodb-table module, never hardcoded."
  type        = string
}

variable "index_arns" {
  description = "Index ARNs, required for A7's Query on GSI1."
  type        = list(string)
  default     = []
}

variable "create_app_user" {
  description = "Create an IAM user for the local cluster to use. The access key is created out of band; see main.tf."
  type        = bool
  default     = false
}

variable "oidc_provider_arn" {
  description = "EKS OIDC provider ARN. When null, no IRSA role is created — which is how the local and LocalStack environments reuse this module."
  type        = string
  default     = null
}

variable "oidc_provider_url" {
  description = "EKS OIDC issuer URL without the https:// scheme, used to build the sub/aud condition keys."
  type        = string
  default     = null

  validation {
    # A trust policy built with the scheme still attached produces condition keys that
    # never match, and the failure mode is an AccessDenied inside the pod with nothing
    # wrong on the Terraform side. Catching it at plan time is worth the four lines.
    condition     = var.oidc_provider_url == null || !startswith(coalesce(var.oidc_provider_url, ""), "https://")
    error_message = "oidc_provider_url must not include the https:// scheme."
  }
}

variable "service_account_namespace" {
  description = "Namespace of the service account allowed to assume the IRSA role."
  type        = string
  default     = "linkpulse"
}

variable "service_account_name" {
  description = "Name of the service account allowed to assume the IRSA role."
  type        = string
  default     = "linkpulse-api"
}

variable "tags" {
  description = "Tags applied to IAM resources."
  type        = map(string)
  default     = {}
}
