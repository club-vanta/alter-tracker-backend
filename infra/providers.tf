terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5"
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
  region     = "us-east-1"
  access_key = var.aws_access_key_id
  secret_key = var.aws_secret_access_key
  default_tags {
    tags = {
      Project     = "alter-tracker"
      ProjectPart = "backend"
      Terraform   = "true"
      Opentofu    = "true"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

