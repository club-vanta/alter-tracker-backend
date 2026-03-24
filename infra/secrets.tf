# ── Secrets Manager ────────────────────────────────────────────────────────────
# Stores sensitive configuration that shouldn't be in code or environment variables.

resource "aws_secretsmanager_secret" "app_secrets" {
  name        = "${local.project_name}/secrets"
  description = "All application secrets: admin credentials and JWT signing key"

  tags = {
    Purpose = "app-config"
  }
}

# The actual secret value must be set manually via AWS Console or CLI:
#   aws secretsmanager put-secret-value \
#     --secret-id alter-tracker-backend/secrets \
#     --secret-string '{
#       "admin_username": "admin",
#       "admin_password": "your-secure-password",
#       "jwt_signing_key": "output-of-openssl-rand-hex-32"
#     }'
#
# Do NOT put the secret value in terraform - it would be stored in state.
