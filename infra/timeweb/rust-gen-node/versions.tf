terraform {
  required_version = ">= 1.6.0"

  required_providers {
    twc = {
      source  = "timeweb-cloud/timeweb-cloud"
      version = "= 1.6.10"
    }
  }
}
