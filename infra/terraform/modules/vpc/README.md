# vpc

Two availability zones (the EKS minimum), a public and a private tier in each, an
internet gateway, per-AZ route tables, gateway endpoints for S3 and DynamoDB, a locked
default security group, and network ACLs. Everything here is free in a real account
except two things, and both are off or avoided by default.

## The cost decision the module comments point here for

| | Private tier + NAT Gateway | Public tier, no NAT |
|---|---|---|
| Hourly | ~$0.045/h for the gateway, plus $0.045/GB processed | ~$0.005/h per public IPv4 address |
| Over 72 hours, two nodes | ~$3.24 before data | ~$0.72 |
| Node reachability | none from the internet | none from the internet either: the node security group admits only the control plane and the load balancer |
| Image pulls, STS, ECR | through NAT (metered per GB) | direct |
| DynamoDB traffic | gateway endpoint, free, private | gateway endpoint, free, private — identical |

The application's data path never crosses the internet in either layout, because the
DynamoDB gateway endpoint is a route-table entry, not a tier. What the private tier buys
is that the nodes have no public address at all, which is the right default for a
cluster that lives longer than its bill cycle and the wrong one for a 72-hour window with
a $25 ceiling. So `enable_nat_gateway` defaults to `false`, the burst's EKS module places
nodes in the public subnets (`node_subnet_tier = "public"`), and the private subnets
exist, are routed (via NAT when it is enabled) and are one variable away.

`map_public_ip_on_launch` is unconditional on the public subnets: the IPv4 charge is per
*assigned* address, not per subnet setting, so an empty public subnet with auto-assign
on bills nothing, and making it a variable would only mean flipping a setting in the
always-free environment under a metered clock.

## Outputs

`vpc_id`, `public_subnet_ids`, `private_subnet_ids`, and `billed_resources` — a list of
whatever in this module costs money per hour. The always-free environment asserts that
list is empty; that assertion is the module's contract.
