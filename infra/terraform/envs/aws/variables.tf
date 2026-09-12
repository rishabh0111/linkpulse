variable "region" {
  description = "Single region for everything, so no cross-region data transfer is ever billed."
  type        = string
  default     = "ap-south-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.42.0.0/16"
}

variable "alert_email" {
  description = <<-EOT
    Where budget alerts go. No default on purpose: a budget guardrail with a placeholder
    address is worse than none, because it looks configured.
  EOT
  type        = string

  validation {
    # POSIX character classes rather than backslash escapes: HCL resolves escape
    # sequences in a quoted string before the regex engine sees them, so "\s" is an HCL
    # error and "\\s" would be a pattern written to survive two layers of quoting.
    # Neither is worth it to say "no spaces".
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must be a valid email address — the budget tripwire is useless without one."
  }
}

variable "burst_budget_usd" {
  description = "Ceiling for the metered burst window. The plan's figure is $25."
  type        = number
  default     = 25
}

variable "repo_url" {
  description = "Repository URL, tagged onto every resource so anything found in the console traces back to code."
  type        = string
  default     = "https://github.com/linkpulse/linkpulse"
}
