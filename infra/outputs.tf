output "instance_public_ipv4" {
  description = "Public IPv4 of the app server (changes on every start)"
  value       = aws_instance.app_server.public_ip
}

output "instance_public_ipv6" {
  description = "Public IPv6 of the app server (stable across starts)"
  value       = aws_instance.app_server.ipv6_addresses[0]
}
