variable "bucket_name" {
  description = "State bucket name. S3 names are globally unique, so this carries a suffix in the env."
  type        = string
}

variable "noncurrent_version_retention_days" {
  description = "How long superseded state versions are kept."
  type        = number
  default     = 90
}

variable "tags" {
  description = "Tags applied to the bucket."
  type        = map(string)
  default     = {}
}
