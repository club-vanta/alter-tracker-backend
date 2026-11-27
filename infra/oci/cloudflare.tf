# Public traffic reaches the server through a remotely managed Cloudflare
# Tunnel: cloudflared (a compose service) dials out to Cloudflare, so the
# instance has no open ports, and the Cloudflare -> origin hop is encrypted.

data "cloudflare_zone" "club_vanta" {
  filter = {
    account_id = var.cloudflare_account_id
    name       = var.cloudflare_domain
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "api" {
  account_id = var.cloudflare_account_id
  name       = local.project_name
  config_src = "cloudflare"
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "api" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.api.id
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "api" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.api.id
  source     = "cloudflare"

  config = {
    ingress = concat(
      [for name in local.tunnel_hostnames : {
        hostname = "${name}.${var.cloudflare_domain}"
        service  = "http://app:8000"
      }],
      # Required catch-all for any other hostname.
      [{ service = "http_status:404" }],
    )
  }
}

resource "cloudflare_dns_record" "api" {
  for_each = toset(local.tunnel_hostnames)

  zone_id = data.cloudflare_zone.club_vanta.id
  name    = each.key
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.api.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # automatic, required when proxied
}
