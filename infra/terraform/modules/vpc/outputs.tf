output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "VPC CIDR, for security group rules built elsewhere."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet IDs. The ALB always lives here; the nodes do too unless NAT is enabled."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs. Usable for the application without NAT thanks to the DynamoDB gateway endpoint."
  value       = aws_subnet.private[*].id
}

output "availability_zones" {
  description = "AZs the subnets span."
  value       = local.azs
}

output "nat_gateway_id" {
  description = "NAT Gateway ID, or null when it was not created. Null here is the desired state outside the burst."
  value       = try(aws_nat_gateway.this[0].id, null)
}

output "billed_resources" {
  description = <<-EOT
    The metered resources this module created, as a plain list. Emitted so `terraform
    output` answers "is anything in my network costing money right now" without opening
    Cost Explorer — the check scripts/cost-report.py automates and the $1 budget alert
    backstops.

    Public IPv4 addresses are not listed: they are billed per assigned address, so they
    appear only once something is actually running in a public subnet, and the eks
    module's cost output is where that shows up.
  EOT
  value = compact([
    var.enable_nat_gateway ? "nat_gateway (~$0.045/hr + $0.045/GB)" : "",
  ])
}
