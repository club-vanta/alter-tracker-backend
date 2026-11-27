# One public subnet used for egress (GHCR, GitHub, OCI APIs, and the
# Cloudflare Tunnel on 7844). Nothing is reachable from the internet:
# public traffic arrives through the tunnel, deploys through Run Command,
# and admin shells through OCI Bastion (bastion.tf).

locals {
  subnet_cidr = "10.0.1.0/24"
}

resource "oci_core_vcn" "this" {
  compartment_id = oci_identity_compartment.this.id
  display_name   = "${local.project_name}-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "altertracker"
  freeform_tags  = local.tags
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = oci_identity_compartment.this.id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${local.project_name}-igw"
  enabled        = true
  freeform_tags  = local.tags
}

resource "oci_core_default_route_table" "this" {
  manage_default_resource_id = oci_core_vcn.this.default_route_table_id

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.this.id
  }
}

# The VCN's default security list allows SSH from anywhere. Taking it over
# replaces its rules: all egress, and SSH only from inside the subnet (the
# bastion endpoint).
resource "oci_core_default_security_list" "this" {
  manage_default_resource_id = oci_core_vcn.this.default_security_list_id
  display_name               = "${local.project_name}-no-public-ingress"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  ingress_security_rules {
    source   = local.subnet_cidr
    protocol = "6" # TCP
    tcp_options {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = oci_identity_compartment.this.id
  vcn_id            = oci_core_vcn.this.id
  display_name      = "${local.project_name}-public"
  cidr_block        = local.subnet_cidr
  dns_label         = "public"
  route_table_id    = oci_core_vcn.this.default_route_table_id
  security_list_ids = [oci_core_vcn.this.default_security_list_id]
  freeform_tags     = local.tags
}
