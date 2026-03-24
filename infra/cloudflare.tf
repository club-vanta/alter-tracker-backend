data "cloudflare_zone" "club_vanta" {
  filter = {
    account_id = var.cloudflare_account_id
    name       = var.cloudflare_domain
  }
}

# NOTE: The subdomain uses a dash (api-alter-tracker) instead of a dot (api.alter-tracker).
# Cloudflare's universal SSL certificate only covers one level of subdomains (*.club-vanta.com).
# A second-level subdomain like api.alter-tracker.club-vanta.com would cause a TLS cipher mismatch.
# See: https://developers.cloudflare.com/ssl/troubleshooting/version-cipher-mismatch/#multi-level-subdomains

# A record — updated each time the instance starts (IPv4 changes on every start)
resource "cloudflare_dns_record" "api_a" {
  zone_id = data.cloudflare_zone.club_vanta.id
  name    = "api-alter-tracker"
  content = aws_instance.app_server.public_ip
  type    = "A"
  proxied = true
  ttl     = 1 # 1 = automatic (required when proxied = true)
}

# AAAA record — stable, IPv6 does not change between starts
resource "cloudflare_dns_record" "api_aaaa" {
  zone_id = data.cloudflare_zone.club_vanta.id
  name    = "api-alter-tracker"
  content = aws_instance.app_server.ipv6_addresses[0]
  type    = "AAAA"
  proxied = true
  ttl     = 1
}

# ── Health check ──────────────────────────────────────────────────────────────
# Disabled: Cloudflare health checks require the Pro plan (or Load Balancing
# add-on) and are not available on the free plan. Set
# local.enable_cloudflare_healthcheck = true to enable if the plan is upgraded.
# In the meantime, use UptimeRobot (free) — see README.
resource "cloudflare_healthcheck" "api" {
  count = local.enable_cloudflare_healthcheck ? 1 : 0

  zone_id     = data.cloudflare_zone.club_vanta.id
  name        = "${local.project_name}-api"
  description = "Monitors the /health endpoint of the Alter Tracker API"
  address     = "api-alter-tracker.${var.cloudflare_domain}"
  type        = "HTTPS"
  interval    = 60
  timeout     = 10
  retries     = 2

  http_config = {
    path           = "/health"
    expected_codes = ["200"]
    method         = "GET"
  }
}

resource "cloudflare_notification_policy" "api_health" {
  count = local.enable_cloudflare_healthcheck ? 1 : 0

  account_id = var.cloudflare_account_id
  name       = "${local.project_name}-api-health-alert"
  alert_type = "health_check_status_notification"
  enabled    = true

  mechanisms = {
    email = [{
      id = "infra-alerts@${var.cloudflare_domain}"
    }]
  }

  filters = {
    health_check_id = [cloudflare_healthcheck.api[0].id]
  }
}
