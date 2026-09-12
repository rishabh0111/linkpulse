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

    # Only for reading the OIDC issuer's certificate to derive the IAM provider's
    # thumbprint. Reading it from the live endpoint rather than pasting a fingerprint
    # means the value cannot go stale when AWS rotates the CA.
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}
