terraform {
  # Timeweb's S3-compatible backend currently supports Terraform 1.5.x only.
  # Keep state remote rather than falling back to an untracked local tfstate.
  required_version = ">= 1.5.3, < 1.6.0"

  backend "s3" {}

  required_providers {
    twc = {
      source  = "timeweb-cloud/timeweb-cloud"
      version = "= 1.6.10"
    }
  }
}
