# The network. Everything in this file is free in a real AWS account except the NAT
# Gateway, which is off by default and gated behind its own variable.
#
# This is the part of the burst that gets built early and kept for the whole credit window:
# VPC, subnets, route tables, internet gateway, NACLs, security groups and gateway
# endpoints cost nothing per hour. Only NAT (~$32.85/mo) and public IPv4 addresses
# (~$3.65/mo each) bill, and both bill whether or not anything uses them.

locals {
  # Two AZs is the floor, not a preference: EKS refuses to create a cluster whose subnets
  # span fewer than two availability zones.
  az_count = 2

  azs = slice(data.aws_availability_zones.available.names, 0, local.az_count)

  # /20 subnets out of a /16 gives 4091 usable addresses each. That is deliberate
  # overshoot — EKS assigns pod IPs from the subnet with the VPC CNI, so a subnet sized
  # for the node count runs out of addresses at roughly 30 pods per node and the failure
  # looks like pods stuck in ContainerCreating for no visible reason.
  public_cidrs  = [for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 4, i)]
  private_cidrs = [for i in range(local.az_count) : cidrsubnet(var.vpc_cidr, 4, i + local.az_count)]
}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr

  # Both are required by the EKS VPC CNI, and a cluster built without them fails in ways
  # that point at DNS rather than at the VPC.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = var.name })
}

# ---------------------------------------------------------------------------------------
# Public tier
# ---------------------------------------------------------------------------------------

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-igw" })
}

resource "aws_subnet" "public" {
  count = local.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.public_cidrs[count.index]
  availability_zone = local.azs[count.index]

  # Unconditionally true, and it costs nothing to leave on. The IPv4 charge
  # (~$0.005/hr, ~$3.65/mo) is per *assigned* address, not per subnet setting — an empty
  # public subnet with auto-assign enabled bills zero, because no address has been
  # handed out. Making this a variable would only mean the burst runbook has to flip a
  # setting in the always-free environment under a metered clock, which is exactly the
  # kind of avoidable step that eats a window.
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.name}-public-${local.azs[count.index]}"

    # The ALB ingress controller discovers subnets by this tag. Without it, an ingress
    # sits in a pending state with an error that does not mention subnets at all.
    "kubernetes.io/role/elb" = "1"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-public" })
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = local.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------------------
# Private tier
#
# These subnets are built whether or not anything runs in them, because the layout is part
# of what this module is demonstrating and it costs nothing to exist. Whether the nodes
# actually land here is a cost decision, documented in the module README.
# ---------------------------------------------------------------------------------------

resource "aws_subnet" "private" {
  count = local.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_cidrs[count.index]
  availability_zone = local.azs[count.index]

  tags = merge(var.tags, {
    Name = "${var.name}-private-${local.azs[count.index]}"

    # The internal-load-balancer counterpart of the public tag above.
    "kubernetes.io/role/internal-elb" = "1"
  })
}

# One route table per AZ rather than one shared table. With a shared table, a
# single-AZ NAT Gateway becomes a cross-AZ hop that is both billed per GB and a
# single point of failure for the other AZ.
resource "aws_route_table" "private" {
  count = local.az_count

  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-private-${local.azs[count.index]}" })
}

resource "aws_route_table_association" "private" {
  count = local.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ---------------------------------------------------------------------------------------
# NAT — the expensive optional piece
#
# ~$0.045/hr per gateway plus $0.045/GB processed, and it bills from creation to deletion
# regardless of traffic. It is one of the two silent charges the plan calls out.
#
# It is not needed for the application's data path: the DynamoDB gateway endpoint below
# gives private subnets free, in-VPC access to the table. NAT is only required for
# outbound internet from private subnets — pulling images from a registry, mostly. That
# is why the burst runs nodes in the public tier instead; see the module README.
# ---------------------------------------------------------------------------------------

resource "aws_eip" "nat" {
  count = var.enable_nat_gateway ? 1 : 0

  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.name}-nat" })
}

resource "aws_nat_gateway" "this" {
  count = var.enable_nat_gateway ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(var.tags, { Name = "${var.name}-nat" })

  # A NAT Gateway in a subnet whose route table has no internet route is created
  # successfully and simply does not work.
  depends_on = [aws_route.public_default]
}

# Deliberately a single NAT shared by both AZs when enabled. The resilient layout is one
# per AZ, which doubles the hourly charge; for a 72-hour window with a documented
# single-AZ trade-off, one is the right call and the reasoning is the deliverable.
resource "aws_route" "private_default" {
  count = var.enable_nat_gateway ? local.az_count : 0

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[0].id
}

# ---------------------------------------------------------------------------------------
# Gateway endpoints — free, and the reason the private tier is useful without NAT
#
# Gateway endpoints (S3 and DynamoDB only) cost nothing: no hourly charge, no data
# processing charge. They inject a prefix-list route into the route tables, so traffic to
# DynamoDB never leaves the AWS network. Interface endpoints, which is what every other
# service needs, are $0.01/hr per AZ — so this trick works for exactly the two services
# this project depends on, which is a coincidence worth being honest about.
# ---------------------------------------------------------------------------------------

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.dynamodb"
  vpc_endpoint_type = "Gateway"

  route_table_ids = concat(
    [aws_route_table.public.id],
    aws_route_table.private[*].id,
  )

  tags = merge(var.tags, { Name = "${var.name}-dynamodb" })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = concat(
    [aws_route_table.public.id],
    aws_route_table.private[*].id,
  )

  tags = merge(var.tags, { Name = "${var.name}-s3" })
}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------------------
# Security groups and NACLs
# ---------------------------------------------------------------------------------------

# Every VPC ships with a default security group that allows all traffic between its own
# members. It cannot be deleted, and anything launched without an explicit group lands in
# it. Managing it with no rules at all means an accidental omission fails closed.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  # No ingress or egress blocks: this is an empty group by intent.

  tags = merge(var.tags, { Name = "${var.name}-default-locked" })
}

# NACLs are stateless — unlike security groups, return traffic needs its own rule. That is
# the single most common way a hand-written NACL breaks a working subnet, so the ephemeral
# port ranges below are load-bearing, not boilerplate.
resource "aws_network_acl" "private" {
  vpc_id     = aws_vpc.this.id
  subnet_ids = aws_subnet.private[*].id

  tags = merge(var.tags, { Name = "${var.name}-private" })
}

resource "aws_network_acl_rule" "private_ingress_vpc" {
  network_acl_id = aws_network_acl.private.id
  rule_number    = 100
  egress         = false
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = var.vpc_cidr
}

# Return traffic for connections the subnet itself opened — to DynamoDB through the
# gateway endpoint, for instance, whose source addresses are outside the VPC CIDR.
resource "aws_network_acl_rule" "private_ingress_ephemeral" {
  network_acl_id = aws_network_acl.private.id
  rule_number    = 110
  egress         = false
  protocol       = "tcp"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
  from_port      = 1024
  to_port        = 65535
}

resource "aws_network_acl_rule" "private_egress_all" {
  network_acl_id = aws_network_acl.private.id
  rule_number    = 100
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}
