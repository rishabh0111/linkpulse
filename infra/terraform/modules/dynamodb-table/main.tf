# The single table from docs/data-model.md.
#
# PROVISIONED billing, never PAY_PER_REQUEST. On-demand has no free tier, and provisioned
# capacity is structurally incapable of billing more than what is set here — which is both
# the cost control and the chaos tool: the table's 20 WCU ceiling is what chaos experiment
# 1 drives k6 past to produce a genuine ProvisionedThroughputExceededException. (20 on the
# table and 5 on the index; variables.tf explains why the split is what it is.)

resource "aws_dynamodb_table" "this" {
  name         = var.table_name
  billing_mode = "PROVISIONED"

  read_capacity  = var.read_capacity
  write_capacity = var.write_capacity

  hash_key  = "PK"
  range_key = "SK"

  # Only key and index attributes are declared. DynamoDB is schemaless for everything
  # else, and declaring an attribute that is not part of a key is an error.
  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  # A7: the owner's links. Sparse — only LINK items carry GSI1PK/GSI1SK, so the click
  # items that make up the bulk of the table cost nothing in index writes.
  global_secondary_index {
    name = "GSI1"

    # key_schema blocks, not the index's hash_key/range_key arguments, which current
    # providers deprecate. Note the table's own hash_key/range_key above are NOT
    # deprecated — there is no top-level key_schema — so the two levels of this one
    # resource genuinely take different syntax.
    key_schema {
      attribute_name = "GSI1PK"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "GSI1SK"
      key_type       = "RANGE"
    }

    projection_type = "INCLUDE"

    # Key attributes are always projected, so code (from PK) and createdAt (from
    # GSI1SK) do not need to be listed. Every name here is a byte written on every
    # link create.
    non_key_attributes = ["longUrl", "active"]

    read_capacity  = var.gsi_read_capacity
    write_capacity = var.gsi_write_capacity
  }

  # Raw CLICK items expire after CLICK_TTL_DAYS; the CLICKSTAT aggregate is the durable
  # record. TTL deletes cost no write capacity, which is why the click feed can be
  # unbounded in principle and still free.
  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  # Off by default: point-in-time recovery is billed per GB-month and is not in the
  # always-free tier. scripts/backup.py covers the actual requirement here — being able
  # to restore this table — at zero cost. Turned on only if there were ever real data.
  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  # There is deliberately no server_side_encryption block.
  #
  # DynamoDB encrypts every table at rest unconditionally, with an AWS-owned key, at no
  # cost. The block exists only to select a customer-managed KMS key; `enabled = false`
  # means "keep the AWS-owned key", which is already the default. Declaring it would
  # change nothing about the table while implying encryption is off, which is the
  # opposite of true.
  #
  # A customer-managed key is $1/month, more than this project's entire out-of-pocket
  # budget, so the AWS-owned key is the right answer on cost as well as on noise.

  tags = var.tags
}
