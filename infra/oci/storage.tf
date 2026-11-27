data "oci_objectstorage_namespace" "this" {
  compartment_id = var.tenancy_ocid
}

# Database dumps from the backup container (every 2 hours, < 1 MB each).
# Always Free: 20 GB and 50,000 requests a month; this uses well under 1 GB
# and about 400 requests.
resource "oci_objectstorage_bucket" "backups" {
  compartment_id = oci_identity_compartment.this.id
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = "${local.project_name}-db-backups"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  freeform_tags  = local.tags
}

# Lifecycle rules act as the Object Storage service, which needs its own
# permission in the compartment.
resource "oci_identity_policy" "objectstorage_lifecycle" {
  compartment_id = oci_identity_compartment.this.id
  name           = "${local.project_name}-objectstorage-lifecycle"
  description    = "Lets Object Storage apply lifecycle rules in this compartment"
  freeform_tags  = local.tags

  statements = [
    "Allow service objectstorage-${var.region} to manage object-family in compartment id ${oci_identity_compartment.this.id}",
  ]
}

resource "oci_objectstorage_object_lifecycle_policy" "backups" {
  namespace = data.oci_objectstorage_namespace.this.namespace
  bucket    = oci_objectstorage_bucket.backups.name

  rules {
    name        = "expire-old-backups"
    action      = "DELETE"
    target      = "objects"
    is_enabled  = true
    time_amount = 90
    time_unit   = "DAYS"
  }

  depends_on = [oci_identity_policy.objectstorage_lifecycle]
}
