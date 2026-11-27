variable "tenancy_ocid" {
  description = "OCID of the OCI tenancy (the `tenancy` value in ~/.oci/config)"
  type        = string
}

variable "region" {
  description = "Home region. Always Free compute only exists there."
  type        = string
  default     = "sa-valparaiso-1"
}

variable "instance_ocpus" {
  description = "A1 OCPUs. Always Free allows 2 in total across the tenancy; the other one is kept for other projects."
  type        = number
  default     = 1

  validation {
    condition     = var.instance_ocpus >= 1 && var.instance_ocpus <= 2
    error_message = "Always Free A1 allows 1 or 2 OCPUs."
  }
}

variable "instance_memory_gbs" {
  description = "A1 memory in GB. Always Free allows 12 in total across the tenancy."
  type        = number
  default     = 6

  validation {
    condition     = var.instance_memory_gbs >= 1 && var.instance_memory_gbs <= 12
    error_message = "Always Free A1 allows up to 12 GB."
  }
}

variable "data_volume_gbs" {
  description = "Block volume mounted at /var/lib/docker (Postgres data and images). Boot (50 GB) plus this must stay under the 200 GB Always Free total, including other projects."
  type        = number
  default     = 50
}

variable "alert_email" {
  description = "Recipient of alarm and budget notifications"
  type        = string
  default     = "infra-alerts@club-vanta.com"
}

variable "deployer_public_key_path" {
  description = "PEM public key of the API key that GitHub Actions uses to start deploys (the private key goes to the GitHub secret OCI_CLI_KEY_CONTENT)"
  type        = string
}

variable "bastion_client_cidrs" {
  description = "Addresses allowed to open Bastion sessions. Sessions also need OCI IAM and an SSH key, so the default allows any address."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "serve_production_hostname" {
  description = "Route api-alter-tracker.<domain> to this instance. Flip to true at cutover, after removing the AWS A/AAAA records."
  type        = bool
  default     = false
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone DNS Edit and Account Cloudflare Tunnel Edit"
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
