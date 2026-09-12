variable "region" {
  description = "Region for the state bucket. Everything else lives here too, so cross-region charges never arise."
  type        = string
  default     = "ap-south-1"
}
