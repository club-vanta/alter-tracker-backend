data "oci_identity_availability_domains" "this" {
  compartment_id = var.tenancy_ocid
}

# Oracle Linux, not Ubuntu: Run Command (the deploy path) only supports
# Oracle Linux, Autonomous Linux, CentOS and Windows.
data "oci_core_images" "oracle_linux_9" {
  compartment_id           = var.tenancy_ocid
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

locals {
  # sa-valparaiso-1 has a single availability domain.
  availability_domain = data.oci_identity_availability_domains.this.availability_domains[0].name
}

resource "oci_core_instance" "app" {
  compartment_id      = oci_identity_compartment.this.id
  availability_domain = local.availability_domain
  display_name        = "${local.project_name}-server"
  shape               = "VM.Standard.A1.Flex"
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.oracle_linux_9.images[0].id
    boot_volume_size_in_gbs = 50
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true # egress only: the security list has no ingress rules
    hostname_label   = "app"
  }

  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  agent_config {
    is_management_disabled = false
    is_monitoring_disabled = false

    plugins_config {
      name          = "Compute Instance Run Command"
      desired_state = "ENABLED"
    }
    plugins_config {
      name          = "Compute Instance Monitoring"
      desired_state = "ENABLED"
    }
    plugins_config {
      name          = "Custom Logs Monitoring"
      desired_state = "ENABLED"
    }
    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }
  }

  metadata = {
    user_data = base64encode(templatefile("${path.module}/cloud-init.sh", {
      alter_deploy_b64 = filebase64("${path.module}/../../deploy/host/alter-deploy")
      oci_cli_version  = "3.95.1"
    }))

    # Read by scripts/deploy.sh on every deploy, so changing them here needs
    # no new instance.
    oci_namespace      = data.oci_objectstorage_namespace.this.namespace
    backup_bucket      = oci_objectstorage_bucket.backups.name
    vault_id           = oci_kms_vault.this.id
    app_secret_name    = local.app_secret_name
    tunnel_secret_name = local.tunnel_secret_name
  }

  lifecycle {
    # A newer image or an edited cloud-init must not replace the server by
    # surprise. Rebuild on purpose with `tofu apply -replace=oci_core_instance.app`.
    ignore_changes = [source_details[0].source_id, metadata["user_data"]]
  }
}

# -- Data volume --
# Mounted at /var/lib/docker by cloud-init: Postgres data survives replacing
# the instance.

resource "oci_core_volume" "data" {
  compartment_id      = oci_identity_compartment.this.id
  availability_domain = local.availability_domain
  display_name        = "${local.project_name}-data"
  size_in_gbs         = var.data_volume_gbs
  freeform_tags       = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_volume_attachment" "data" {
  attachment_type = "paravirtualized"
  instance_id     = oci_core_instance.app.id
  volume_id       = oci_core_volume.data.id
  device          = "/dev/oracleoci/oraclevdb"
}

# Weekly backups kept 3 weeks: at most 4 exist at once (Always Free allows 5).
resource "oci_core_volume_backup_policy" "weekly" {
  compartment_id = oci_identity_compartment.this.id
  display_name   = "${local.project_name}-weekly"
  freeform_tags  = local.tags

  schedules {
    backup_type       = "INCREMENTAL"
    period            = "ONE_WEEK"
    day_of_week       = "SUNDAY"
    hour_of_day       = 7
    offset_type       = "STRUCTURED"
    time_zone         = "UTC"
    retention_seconds = 3 * 7 * 24 * 3600
  }
}

resource "oci_core_volume_backup_policy_assignment" "data" {
  asset_id  = oci_core_volume.data.id
  policy_id = oci_core_volume_backup_policy.weekly.id
}
