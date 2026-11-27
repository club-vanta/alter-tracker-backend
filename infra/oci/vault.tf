# Default (shared) vault with a software-protected key: both Always Free.

resource "oci_kms_vault" "this" {
  compartment_id = oci_identity_compartment.this.id
  display_name   = "${local.project_name}-vault"
  vault_type     = "DEFAULT"
  freeform_tags  = local.tags
}

resource "oci_kms_key" "secrets" {
  compartment_id      = oci_identity_compartment.this.id
  display_name        = "${local.project_name}-secrets-key"
  management_endpoint = oci_kms_vault.this.management_endpoint
  protection_mode     = "SOFTWARE"
  freeform_tags       = local.tags

  key_shape {
    algorithm = "AES"
    length    = 32
  }
}

# App secrets: admin_username, admin_password, jwt_signing_key, db_user,
# db_password. Tofu only creates the container with an empty JSON object;
# the real values are set outside tofu so they never reach the state (see
# README, "Production secrets").
resource "oci_vault_secret" "app" {
  compartment_id = oci_identity_compartment.this.id
  vault_id       = oci_kms_vault.this.id
  key_id         = oci_kms_key.secrets.id
  secret_name    = local.app_secret_name
  description    = "Application secrets read by scripts/deploy.sh"
  freeform_tags  = local.tags

  secret_content {
    content_type = "BASE64"
    content      = base64encode("{}")
  }

  lifecycle {
    ignore_changes = [secret_content]
  }
}

# The tunnel token is already in the state (Cloudflare data source), so it
# is written from here.
resource "oci_vault_secret" "tunnel_token" {
  compartment_id = oci_identity_compartment.this.id
  vault_id       = oci_kms_vault.this.id
  key_id         = oci_kms_key.secrets.id
  secret_name    = local.tunnel_secret_name
  description    = "Cloudflare Tunnel token for the cloudflared container"
  freeform_tags  = local.tags

  secret_content {
    content_type = "BASE64"
    content      = base64encode(data.cloudflare_zero_trust_tunnel_cloudflared_token.api.token)
  }
}
