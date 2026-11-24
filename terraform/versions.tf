# Which tools and versions Terraform needs.
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# Every resource gets a "Project" tag so you can find (and bill) it easily.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = var.project
    }
  }
}
