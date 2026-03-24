data "cloudflare_zone" "club_vanta" {
  filter = {
    account_id = var.cloudflare_account_id
    name       = var.cloudflare_domain
  }
}

# A record — updated each time the instance starts (IPv4 changes on every start)
resource "cloudflare_dns_record" "api_a" {
  zone_id = data.cloudflare_zone.club_vanta.id
  name    = "api.alter-tracker"
  content = aws_instance.app_server.public_ip
  type    = "A"
  proxied = true
  ttl     = 1 # 1 = automatic (required when proxied = true)
}

# AAAA record — stable, IPv6 does not change between starts
resource "cloudflare_dns_record" "api_aaaa" {
  zone_id = data.cloudflare_zone.club_vanta.id
  name    = "api.alter-tracker"
  content = aws_instance.app_server.ipv6_addresses[0]
  type    = "AAAA"
  proxied = true
  ttl     = 1
}

# ── Health check ──────────────────────────────────────────────────────────────
# Cloudflare pings /health every 60 seconds and emails if the app goes down.
# Note: checks the proxied URL (end-to-end test through Cloudflare → origin).
resource "cloudflare_healthcheck" "api" {
  zone_id     = data.cloudflare_zone.club_vanta.id
  name        = "${local.project_name}-api"
  description = "Monitors the /health endpoint of the Alter Tracker API"
  address     = "api.alter-tracker.${var.cloudflare_domain}"
  type        = "HTTPS"
  path        = "/health"

  expected_codes = ["200"]
  method         = "GET"

  interval = 60 # minimum interval on free plan
  timeout  = 10
  retries  = 2

  notification_suspended       = false
  notification_email_addresses = ["infra-alerts@${var.cloudflare_domain}"]
}
