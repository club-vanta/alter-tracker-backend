terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6"
    }
  }

  backend "s3" {
    bucket = "alter-tracker-terraform-state"
    key    = "backend.tfstate"
    region = "us-east-1"

    # We no longer need DynamoDB for state locking
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = {
      Project     = "alter-tracker"
      ProjectPart = "backend"
      Terraform   = "true"
      Opentofu    = "true"
    }
  }
}
