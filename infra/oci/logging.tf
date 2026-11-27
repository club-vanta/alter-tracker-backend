# Container and host logs go to OCI Logging through the Unified Monitoring
# Agent (the "Custom Logs Monitoring" plugin). Docker keeps rotated local
# copies too, so `docker logs` still works on the server.

resource "oci_logging_log_group" "this" {
  compartment_id = oci_identity_compartment.this.id
  display_name   = local.project_name
  description    = "Alter Tracker backend logs"
  freeform_tags  = local.tags
}

resource "oci_logging_log" "server" {
  log_group_id       = oci_logging_log_group.this.id
  display_name       = "server"
  log_type           = "CUSTOM"
  is_enabled         = true
  retention_duration = 30
  freeform_tags      = local.tags
}

resource "oci_logging_unified_agent_configuration" "server" {
  compartment_id = oci_identity_compartment.this.id
  display_name   = "${local.project_name}-server"
  description    = "Ships Docker container logs and /var/log/alter-tracker to the server log"
  is_enabled     = true
  freeform_tags  = local.tags

  service_configuration {
    configuration_type = "LOGGING"

    destination {
      log_object_id = oci_logging_log.server.id
    }

    # docker json-file lines: {"log": "<app JSON line>", "stream": ...,
    # "attrs": {"com.docker.compose.service": "<service>"}, "time": ...}
    sources {
      name        = "containers"
      source_type = "LOG_TAIL"
      paths       = ["/var/lib/docker/containers/*/*-json.log"]

      parser {
        parser_type = "JSON"
      }
    }

    # deploy.log and metrics.log, plain text.
    sources {
      name        = "host"
      source_type = "LOG_TAIL"
      paths       = ["/var/log/alter-tracker/*.log"]

      parser {
        parser_type = "NONE"
      }
    }
  }

  group_association {
    group_list = [oci_identity_dynamic_group.instance.id]
  }
}
