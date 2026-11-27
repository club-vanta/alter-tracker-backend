# -- Instance identity --
# The instance authenticates as an instance principal: no keys on disk.

resource "oci_identity_dynamic_group" "instance" {
  compartment_id = var.tenancy_ocid
  name           = "${local.project_name}-instance"
  description    = "Instances in the alter-tracker compartment"
  matching_rule  = "ALL {instance.compartment.id = '${oci_identity_compartment.this.id}'}"
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "instance" {
  compartment_id = oci_identity_compartment.this.id
  name           = "${local.project_name}-instance"
  description    = "What the server may do with its instance principal"
  freeform_tags  = local.tags

  statements = [
    # deploy.sh reads the app and tunnel secrets.
    "Allow dynamic-group ${oci_identity_dynamic_group.instance.name} to read secret-bundles in compartment id ${oci_identity_compartment.this.id} where target.vault.id = '${oci_kms_vault.this.id}'",
    # Run Command: the agent fetches and reports commands for itself only.
    "Allow dynamic-group ${oci_identity_dynamic_group.instance.name} to use instance-agent-command-execution-family in compartment id ${oci_identity_compartment.this.id} where request.instance.id = target.instance.id",
    # Unified Monitoring Agent ships logs.
    "Allow dynamic-group ${oci_identity_dynamic_group.instance.name} to use log-content in compartment id ${oci_identity_compartment.this.id}",
    # push-metrics.sh publishes custom metrics.
    "Allow dynamic-group ${oci_identity_dynamic_group.instance.name} to use metrics in compartment id ${oci_identity_compartment.this.id} where target.metrics.namespace = '${local.metric_namespace}'",
    # The backup container writes dumps.
    "Allow dynamic-group ${oci_identity_dynamic_group.instance.name} to read buckets in compartment id ${oci_identity_compartment.this.id} where target.bucket.name = '${oci_objectstorage_bucket.backups.name}'",
    "Allow dynamic-group ${oci_identity_dynamic_group.instance.name} to manage objects in compartment id ${oci_identity_compartment.this.id} where target.bucket.name = '${oci_objectstorage_bucket.backups.name}'",
  ]
}

# -- GitHub Actions deployer --
# API key user whose only power is creating Run Command commands in this
# compartment. The commands can only run `alter-deploy <sha>` as root
# (see cloud-init.sh), and alter-deploy only accepts commits in main's
# history, so a leaked key can at most redeploy a commit from main.

resource "oci_identity_user" "deployer" {
  compartment_id = var.tenancy_ocid
  name           = "${local.project_name}-deployer"
  description    = "GitHub Actions: starts deploys through Run Command"
  email          = var.alert_email
  freeform_tags  = local.tags
}

resource "oci_identity_user_capabilities_management" "deployer" {
  user_id                      = oci_identity_user.deployer.id
  can_use_api_keys             = true
  can_use_auth_tokens          = false
  can_use_console_password     = false
  can_use_customer_secret_keys = false
  can_use_smtp_credentials     = false
}

resource "oci_identity_api_key" "deployer" {
  user_id   = oci_identity_user.deployer.id
  key_value = file(pathexpand(var.deployer_public_key_path))
}

resource "oci_identity_group" "deployers" {
  compartment_id = var.tenancy_ocid
  name           = "${local.project_name}-deployers"
  description    = "May start Run Command deploys on the alter-tracker server"
  freeform_tags  = local.tags
}

resource "oci_identity_user_group_membership" "deployer" {
  group_id = oci_identity_group.deployers.id
  user_id  = oci_identity_user.deployer.id
}

resource "oci_identity_policy" "deployer" {
  compartment_id = oci_identity_compartment.this.id
  name           = "${local.project_name}-deployer"
  description    = "Run Command for GitHub Actions deploys"
  freeform_tags  = local.tags

  statements = [
    "Allow group ${oci_identity_group.deployers.name} to manage instance-agent-command-family in compartment id ${oci_identity_compartment.this.id}",
  ]
}
