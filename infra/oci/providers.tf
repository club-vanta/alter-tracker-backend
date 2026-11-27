terraform {
  required_version = ">= 1.10"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 9"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5"
    }
  }

  # OpenTofu has no native OCI backend, so the state lives in OCI Object
  # Storage through its S3-compatible API. The bucket is created once by
  # hand (see README, "Infrastructure (OCI)"), and the Customer Secret Key
  # lives in its own credentials file, apart from ~/.aws/credentials.
  backend "s3" {
    bucket = "alter-tracker-tofu-state"
    key    = "oci.tfstate"
    region = "sa-valparaiso-1"
    endpoints = {
      s3 = "https://ax7nqesq2qs0.compat.objectstorage.sa-valparaiso-1.oraclecloud.com"
    }
    shared_credentials_files = ["~/.oci/tofu-state-credentials"]

    skip_region_validation      = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}

provider "oci" {
  region              = var.region
  config_file_profile = "DEFAULT"
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
