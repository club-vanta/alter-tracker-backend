# Admin shell access (cutover, restores, incidents) through OCI Bastion,
# which is free. Run Command cannot do this: its user may only run
# alter-deploy, so a leaked deploy key cannot get root.
#
# A managed SSH session puts a temporary key on the instance (Bastion
# plugin) and opens a tunnel that lasts at most 3 hours. The server only
# accepts SSH from inside the subnet, where the bastion endpoint lives.

resource "oci_bastion_bastion" "this" {
  compartment_id               = oci_identity_compartment.this.id
  bastion_type                 = "STANDARD"
  name                         = "alterTrackerBastion"
  target_subnet_id             = oci_core_subnet.public.id
  client_cidr_block_allow_list = var.bastion_client_cidrs
  max_session_ttl_in_seconds   = 3 * 3600
  freeform_tags                = local.tags
}
