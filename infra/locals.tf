locals {
  project_name = "alter-tracker-backend"

  # Cloudflare health checks require the Pro plan (or Load Balancing add-on).
  # Disabled on the free plan — use UptimeRobot instead (see README).
  enable_cloudflare_healthcheck = false
}
