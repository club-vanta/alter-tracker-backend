# -- Compartment --
# Everything for this project lives here, so quotas and policies can be
# scoped to it and the rest of the tenancy stays free for other projects.

resource "oci_identity_compartment" "this" {
  compartment_id = var.tenancy_ocid
  name           = "alter-tracker"
  description    = "Alter Tracker backend (production)"
  enable_delete  = true
  freeform_tags  = local.tags
}

# -- Quotas --
# Safety net against creating anything outside Always Free. Statements are
# evaluated in order and a later one supersedes an earlier one for the same
# resource, so "zero everything, then allow A1" leaves only A1.

resource "oci_limits_quota" "free_tier" {
  compartment_id = var.tenancy_ocid
  name           = "alter-tracker-free-tier"
  description    = "Keeps the alter-tracker compartment inside the Always Free limits"
  freeform_tags  = local.tags

  statements = [
    "zero compute-core quotas in compartment ${oci_identity_compartment.this.name}",
    "zero compute-memory quotas in compartment ${oci_identity_compartment.this.name}",
    "set compute-core quota standard-a1-core-count to ${var.instance_ocpus} in compartment ${oci_identity_compartment.this.name}",
    "set compute-memory quota standard-a1-memory-count to ${var.instance_memory_gbs} in compartment ${oci_identity_compartment.this.name}",
    # A1 is also counted per region; "zero ... quotas" zeroes these too.
    "set compute-core quota standard-a1-core-regional-count to ${var.instance_ocpus} in compartment ${oci_identity_compartment.this.name}",
    "set compute-memory quota standard-a1-memory-regional-count to ${var.instance_memory_gbs} in compartment ${oci_identity_compartment.this.name}",
    "set block-storage quota total-storage-gb to ${50 + var.data_volume_gbs} in compartment ${oci_identity_compartment.this.name}",
    "set block-storage quota backup-count to 5 in compartment ${oci_identity_compartment.this.name}",
  ]
}

# -- Budget --
# Covers the whole tenancy: Always Free usage costs nothing, so any actual
# spend means something outside the free tier was created.

resource "oci_budget_budget" "tenancy" {
  compartment_id = var.tenancy_ocid
  display_name   = "tenancy-zero-spend"
  description    = "Alerts on any spend: everything in this tenancy should be Always Free"
  amount         = 1
  reset_period   = "MONTHLY"
  target_type    = "COMPARTMENT"
  targets        = [var.tenancy_ocid]
  freeform_tags  = local.tags
}

resource "oci_budget_alert_rule" "any_spend" {
  budget_id      = oci_budget_budget.tenancy.id
  display_name   = "any-actual-spend"
  type           = "ACTUAL"
  threshold_type = "ABSOLUTE"
  threshold      = 0.01
  recipients     = var.alert_email
  message        = "OCI is charging money. Something outside Always Free exists: check Billing > Cost analysis."
}

resource "oci_budget_alert_rule" "forecast" {
  budget_id      = oci_budget_budget.tenancy.id
  display_name   = "forecast-over-1"
  type           = "FORECAST"
  threshold_type = "ABSOLUTE"
  threshold      = 1
  recipients     = var.alert_email
  message        = "OCI forecasts spend this month. Something outside Always Free exists: check Billing > Cost analysis."
}
