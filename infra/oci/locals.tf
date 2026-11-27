locals {
  project_name = "alter-tracker-backend"

  # Hostnames served by the tunnel. The test one exists from the start so
  # the instance can be checked before the cutover. Single-level names only:
  # the universal certificate covers *.club-vanta.com, not deeper levels.
  test_hostname       = "api-alter-tracker-oci"
  production_hostname = "api-alter-tracker"
  tunnel_hostnames = concat(
    [local.test_hostname],
    var.serve_production_hostname ? [local.production_hostname] : [],
  )

  app_secret_name    = "${local.project_name}-secrets"
  tunnel_secret_name = "${local.project_name}-tunnel-token"

  # Custom metrics published by deploy/host/push-metrics.sh.
  metric_namespace = "alter_tracker"

  tags = {
    Project     = "alter-tracker"
    ProjectPart = "backend"
    Opentofu    = "true"
  }
}
