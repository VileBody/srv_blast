output "server_id" {
  value       = twc_server.rust_gen.id
  description = "Timeweb server ID."
}

output "server_name" {
  value       = twc_server.rust_gen.name
  description = "Rust renderer node name."
}

output "public_ipv4" {
  value = coalesce(
    try(twc_server_ip.public_ipv4[0].ip, null),
    try(twc_server.rust_gen.main_ipv4, null),
    null,
  )
  description = "Public IPv4 when enabled."
}

output "manager_url" {
  value = coalesce(
    try(twc_server_ip.public_ipv4[0].ip, null),
    try(twc_server.rust_gen.main_ipv4, null),
    null,
  ) != null ? "http://${coalesce(try(twc_server_ip.public_ipv4[0].ip, null), try(twc_server.rust_gen.main_ipv4, null))}:${var.manager_api_port}" : null
  description = "Candidate manager URL. Keep it private or behind a proxy in production."
}

output "firewall_id" {
  value       = twc_firewall.rust_gen.id
  description = "Dedicated firewall ID."
}
