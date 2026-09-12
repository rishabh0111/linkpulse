variable "table_name" {
  description = "Name of the single table."
  type        = string
}

# The always-free allowance is 25 RCU and 25 WCU for the whole account, and an index's
# provisioned capacity counts against it exactly as the table's does. So the four numbers
# below have to sum to 25 per side, and the validations check the SUM -- checking the
# table alone was the mistake this file used to make: 25 on the table plus 5 on GSI1 is
# 30, which bills for 5 units on each side, roughly $3 a month, on an environment whose
# defining property is costing nothing. Found by scripts/metered-resources.py in phase 8,
# which now guards it from the other direction.

variable "read_capacity" {
  description = "Table RCU. With GSI1's share, the two must stay within the 25 RCU account-wide always-free allowance."
  type        = number
  default     = 20

  validation {
    # A guard against the one mistake that actually costs money here. Anything above the
    # free allowance bills immediately, and a typo in a tfvars file is exactly how that
    # happens.
    condition     = var.read_capacity > 0 && var.read_capacity + var.gsi_read_capacity <= 25
    error_message = "read_capacity + gsi_read_capacity must be 1-25 to stay inside the always-free allowance."
  }
}

variable "write_capacity" {
  description = "Table WCU. 20 here plus 5 on GSI1 is the free allowance; the table's 20 is the ceiling chaos experiment 1 drives k6 past."
  type        = number
  default     = 20

  validation {
    condition     = var.write_capacity > 0 && var.write_capacity + var.gsi_write_capacity <= 25
    error_message = "write_capacity + gsi_write_capacity must be 1-25 to stay inside the always-free allowance."
  }
}

variable "gsi_read_capacity" {
  description = "GSI1 RCU. Provisioned separately from the table's, billed and allowed identically."
  type        = number
  default     = 5
}

variable "gsi_write_capacity" {
  description = "GSI1 WCU. Only LINK items carry the index keys, so 5 is generous: A2 is the only pattern that writes it."
  type        = number
  default     = 5
}

variable "point_in_time_recovery" {
  description = "PITR is billed per GB-month and is not in the always-free tier. See the comment in main.tf."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to the table."
  type        = map(string)
  default     = {}
}
