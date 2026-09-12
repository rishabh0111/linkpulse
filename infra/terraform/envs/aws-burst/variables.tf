variable "region" {
  description = "Must match envs/aws, or the cluster cannot use its subnets."
  type        = string
  default     = "ap-south-1"
}

variable "state_bucket" {
  description = <<-EOT
    Bucket holding envs/aws state, read through terraform_remote_state.

    Unlike the backend block, a data source CAN interpolate — but it still cannot read the
    backend's own configuration, so the bucket has to be supplied here as well. Set it in
    terraform.tfvars alongside the backend.hcl used at init.
  EOT
  type        = string
}

variable "kubernetes_version" {
  description = "Control plane version. Keep it aligned with the k3d version in .tool-versions so the manifests are exercised against the same API."
  type        = string
  default     = "1.34"
}

variable "service_account_namespace" {
  description = "Namespace of the service account that assumes the IRSA role."
  type        = string
  default     = "linkpulse"
}

variable "service_account_name" {
  description = <<-EOT
    Service account that assumes the IRSA role. Must match k8s/manifests/base/serviceaccount.yaml
    exactly: the trust policy's `sub` condition is system:serviceaccount:<namespace>:<this>, and a
    mismatch is an AccessDenied inside the pod with nothing wrong on the Terraform side. This
    defaulted to "linkpulse-api" until phase 11, while the manifest has always said "linkpulse";
    caught while writing the burst runbook, before it could cost metered time.
  EOT
  type        = string
  default     = "linkpulse"
}

variable "repo_url" {
  description = "Repository URL, tagged onto every resource."
  type        = string
  default     = "https://github.com/linkpulse/linkpulse"
}
