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

variable "serve_from_aws" {
  description = "Point api-alter-tracker at the EC2 instance. Set to false at the OCI cutover (see README, \"Cutover\")."
  type        = bool
  default     = true
}
