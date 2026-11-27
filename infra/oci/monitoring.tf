# -- Notifications --

resource "oci_ons_notification_topic" "alerts" {
  compartment_id = oci_identity_compartment.this.id
  name           = "${local.project_name}-alerts"
  description    = "Alarm notifications for the Alter Tracker server"
  freeform_tags  = local.tags
}

# The address gets a confirmation email that must be accepted once.
resource "oci_ons_subscription" "email" {
  compartment_id = oci_identity_compartment.this.id
  topic_id       = oci_ons_notification_topic.alerts.id
  protocol       = "EMAIL"
  endpoint       = var.alert_email
}

# -- Alarms --

locals {
  instance_filter = "{resourceId = \"${oci_core_instance.app.id}\"}"

  alarms = {
    cpu-high = {
      namespace = "oci_computeagent"
      query     = "CpuUtilization[5m]${local.instance_filter}.mean() > 80"
      pending   = "PT10M"
      severity  = "WARNING"
      body      = "CPU above 80% for 10 minutes."
    }
    memory-high = {
      namespace = "oci_computeagent"
      query     = "MemoryUtilization[5m]${local.instance_filter}.mean() > 85"
      pending   = "PT10M"
      severity  = "WARNING"
      body      = "Memory above 85% for 10 minutes."
    }
    # Fires when the instance stops reporting: stopped, crashed, or reclaimed
    # by Oracle as idle. Rebuild steps are in the README.
    instance-absent = {
      namespace = "oci_computeagent"
      query     = "CpuUtilization[5m]${local.instance_filter}.absent()"
      pending   = "PT10M"
      severity  = "CRITICAL"
      body      = "The server stopped reporting metrics. Check that the instance exists and is running."
    }
    disk-high = {
      namespace = local.metric_namespace
      query     = "disk_used_pct[5m].max() > 80"
      pending   = "PT5M"
      severity  = "WARNING"
      body      = "A disk on the server is above 80%. The mount is in the metric dimensions."
    }
    log-error = {
      namespace = local.metric_namespace
      query     = "log_errors[5m].sum() >= 1"
      pending   = "PT1M"
      severity  = "ERROR"
      body      = "Error-level log lines in the last 5 minutes. Search the server log in OCI Logging."
    }
    log-warning = {
      namespace = local.metric_namespace
      query     = "log_warnings[10m].sum() >= 10"
      pending   = "PT1M"
      severity  = "WARNING"
      body      = "10 or more warning-level log lines in 10 minutes."
    }
    # push-metrics.sh stopped publishing (cron, credentials, or the OCI CLI):
    # the disk and log alarms above would stay silent.
    metrics-absent = {
      namespace = local.metric_namespace
      query     = "disk_used_pct[10m].absent()"
      pending   = "PT15M"
      severity  = "WARNING"
      body      = "Custom metrics stopped arriving. Check /var/log/alter-tracker/metrics.log on the server."
    }
  }
}

resource "oci_monitoring_alarm" "this" {
  for_each = local.alarms

  compartment_id        = oci_identity_compartment.this.id
  metric_compartment_id = oci_identity_compartment.this.id
  display_name          = "${local.project_name}-${each.key}"
  namespace             = each.value.namespace
  query                 = each.value.query
  pending_duration      = each.value.pending
  severity              = each.value.severity
  body                  = each.value.body
  destinations          = [oci_ons_notification_topic.alerts.id]
  is_enabled            = true
  freeform_tags         = local.tags
}
