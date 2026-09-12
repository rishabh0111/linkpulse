terraform {
  required_version = ">= 1.13"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Modules constrain loosely and the environments pin exactly. A hard pin in a
      # module would fight every other module the moment one of them needs a newer
      # provider; the lock file in each env is what actually makes builds reproducible.
      version = "~> 6.0"
    }
  }
}
