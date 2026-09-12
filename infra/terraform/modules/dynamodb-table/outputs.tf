output "table_name" {
  description = "Table name, for the application's DDB_TABLE."
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "Table ARN. The IAM module scopes its policy to exactly this."
  value       = aws_dynamodb_table.this.arn
}

output "index_arns" {
  description = <<-EOT
    ARNs of the table's indexes. A policy granting Query on the table ARN alone does not
    cover the index — an index is a separate resource ARN, so omitting it makes A7 fail
    with AccessDenied at runtime and nowhere earlier.
  EOT
  value       = ["${aws_dynamodb_table.this.arn}/index/*"]
}

output "provisioned_write_capacity" {
  description = "Echoed so the chaos experiment can assert it is driving load against the ceiling it thinks it is."
  value       = aws_dynamodb_table.this.write_capacity
}
