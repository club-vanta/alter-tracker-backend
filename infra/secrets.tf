# ── Secrets Manager ────────────────────────────────────────────────────────────
# Stores sensitive configuration that shouldn't be in code or environment variables.

resource "aws_secretsmanager_secret" "admin_credentials" {
  name        = "${local.project_name}/admin-credentials"
  description = "Initial admin user credentials for the Alter Tracker API"

  tags = {
    Purpose = "admin-seed"
  }
}

# The actual secret value must be set manually via AWS Console or CLI:
#   aws secretsmanager put-secret-value \
#     --secret-id alter-tracker-backend/admin-credentials \
#     --secret-string '{"username":"admin","password":"your-secure-password"}'
#
# Do NOT put the secret value in terraform - it would be stored in state.

resource "aws_secretsmanager_secret" "app_secrets" {
  name        = "${local.project_name}/app-secrets"
  description = "Application secrets (JWT secret key, etc.)"

  tags = {
    Purpose = "app-config"
  }
}

# Set via:
#   aws secretsmanager put-secret-value \
#     --secret-id alter-tracker-backend/app-secrets \
#     --secret-string '{"secret_key":"your-jwt-secret-from-openssl-rand-hex-32"}'
