# Cost guardrails.
#
# These are singletons — one budget per account, not one per component — so they live in
# the environment rather than in a reusable module. A module for a resource that can only
# ever exist once is indirection with nothing on the other side of it.
#
# This is the first thing applied in the whole project, before the table and before any
# application code. The plan's order of work puts guardrails at step one for a reason: a
# budget alert configured after the mistake is a report, not a guardrail.

# The $1 alert. One dollar is not a spending limit — it is a tripwire.
#
# The always-free tier should keep this account at exactly $0.00, so any charge at all
# means something metered is running that I did not intend. A threshold set at, say, $20
# would be a sensible budget and a useless alarm: it would fire days after a NAT Gateway
# started billing. At $1 the alert means "something is wrong right now".
resource "aws_budgets_budget" "tripwire" {
  name         = "linkpulse-tripwire"
  budget_type  = "COST"
  limit_amount = "1.0"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Fires on actual spend crossing 1% of $1, i.e. one cent. AWS requires a percentage
  # rather than an absolute trigger, so the limit is set to $1 and the threshold to the
  # smallest meaningful fraction of it.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 1
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # And a forecast trigger, because ACTUAL is inherently late: NAT Gateway charges accrue
  # for hours before they cross a cent. FORECASTED catches the burn rate rather than the
  # accumulated total, which is the difference between noticing on day one and noticing
  # on day three.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# The burst budget, separate from the tripwire.
#
# During the 72-hour window the tripwire will be firing continuously by design — EKS is
# supposed to be costing money then. This one is the number that actually matters: $25 is
# the ceiling the plan set, and the 50% notification is the point at which the window
# should be reviewed rather than continued on autopilot.
resource "aws_budgets_budget" "burst_ceiling" {
  name         = "linkpulse-burst-ceiling"
  budget_type  = "COST"
  limit_amount = tostring(var.burst_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # Forecast at 100%: the signal to destroy now rather than at the end of the window.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
