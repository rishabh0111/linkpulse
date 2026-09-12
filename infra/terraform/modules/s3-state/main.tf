# The Terraform state backend: an S3 bucket, and nothing else.
#
# ---------------------------------------------------------------------------------------
# A deliberate deviation from the plan, which called for "S3 + DynamoDB lock table".
#
# That was the canonical pattern for years, but it is no longer: the S3 backend's
# `dynamodb_table` argument is deprecated in favour of `use_lockfile`, which takes a lock
# by conditional-writing a `.tflock` object next to the state. Two reasons to move, and the
# second one is specific to this project:
#
#  1. It is what current Terraform documents. Shipping the deprecated pattern in a
#     portfolio repo would be shipping a thing I would have to explain away.
#  2. **A lock table would eat the free tier the application needs.** The 25 RCU / 25 WCU
#     always-free DynamoDB allowance is per account, not per table. A second table would
#     take a slice of it, which means either the app table drops below 25 WCU or the
#     account starts billing. Chaos experiment 1 depends on 25 WCU being the app table's
#     ceiling alone — so a lock table would literally cost me the throttle experiment.
#
# The locking guarantee is not weaker: S3 conditional writes are strongly consistent, so a
# second apply fails to create the lock object rather than racing.
# ---------------------------------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = var.bucket_name

  tags = var.tags
}

# Versioning is the actual disaster recovery for Terraform state. A corrupt or truncated
# state file is recoverable by restoring the previous version; without versioning it is
# recoverable from nothing.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# State files contain every attribute of every resource, which routinely includes
# generated secrets. SSE-S3 is free; the alternative (SSE-KMS with a customer key) is
# $1/month.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }

    # Reuses one data key across objects instead of calling KMS per object. Free either
    # way with SSE-S3, but it is the correct setting and costs nothing to be right about.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# BucketOwnerEnforced disables ACLs entirely, so access is decided by policy alone. This
# is the modern default and removes the whole class of "public via ACL" mistakes.
resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Every apply writes a new state version. Unbounded, that grows forever against a 5 GB
# free allowance shared with everything else in S3. 90 days is far longer than any
# rollback window I would actually use.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    # Required by the provider: an empty filter means the rule applies to all objects.
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_retention_days
    }

    # A lock object left behind by an interrupted apply is garbage after a day. This
    # does not release a live lock — `terraform force-unlock` does that — it just stops
    # abandoned ones accumulating.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # The provider will happily write a lifecycle rule before versioning is on, and
  # noncurrent-version expiry on an unversioned bucket silently does nothing.
  depends_on = [aws_s3_bucket_versioning.state]
}

# TLS-only. Without this, a plain-HTTP PutObject of the state file is accepted.
resource "aws_s3_bucket_policy" "require_tls" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.require_tls.json

  # Applying a bucket policy before the public access block leaves a window, however
  # short, where a policy could grant public access.
  depends_on = [aws_s3_bucket_public_access_block.state]
}

data "aws_iam_policy_document" "require_tls" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}
