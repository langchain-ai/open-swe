terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.2.0, < 7"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
