variable "cloudflare_api_token" {
  description = "Cloudflare API token — set via TF_VAR_cloudflare_api_token env var or in terraform.tfvars"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare Account ID"
  type        = string
  sensitive   = true
}

variable "cloudflare_domain" {
  description = "Domain name"
  type        = string
  default     = "club-vanta.com"
}
