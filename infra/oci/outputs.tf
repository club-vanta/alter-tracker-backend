# Values for the GitHub "production" environment (see README, "Continuous
# deployment").

output "compartment_id" {
  description = "GitHub variable OCI_COMPARTMENT_ID"
  value       = oci_identity_compartment.this.id
}

output "instance_id" {
  description = "GitHub variable OCI_INSTANCE_ID"
  value       = oci_core_instance.app.id
}

output "deployer_user_id" {
  description = "GitHub secret OCI_CLI_USER"
  value       = oci_identity_user.deployer.id
}

output "deployer_key_fingerprint" {
  description = "GitHub secret OCI_CLI_FINGERPRINT"
  value       = oci_identity_api_key.deployer.fingerprint
}

output "vault_id" {
  description = "Vault holding the app secret (used when populating it)"
  value       = oci_kms_vault.this.id
}

output "app_secret_id" {
  description = "Secret to populate with the app secrets JSON"
  value       = oci_vault_secret.app.id
}

output "test_url" {
  description = "Health check through the tunnel's test hostname"
  value       = "https://${local.test_hostname}.${var.cloudflare_domain}/health"
}

output "bastion_id" {
  description = "Bastion for admin SSH sessions (see CLAUDE.md, \"Shell en el servidor\")"
  value       = oci_bastion_bastion.this.id
}
