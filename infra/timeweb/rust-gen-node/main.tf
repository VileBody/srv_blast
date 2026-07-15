provider "twc" {
  token = trimspace(var.twc_token) != "" ? trimspace(var.twc_token) : null
}

resource "twc_server" "rust_gen" {
  name                      = var.server_name
  comment                   = "Managed by Terraform: isolated Rust renderer worker"
  project_id                = var.project_id
  os_id                     = var.os_id
  preset_id                 = var.preset_id
  availability_zone         = var.availability_zone
  ssh_keys_ids              = var.ssh_key_ids
  is_root_password_required = length(var.ssh_key_ids) == 0
  cloud_init = templatefile("${path.module}/templates/bootstrap-cloud-init.yaml.tftpl", {
    rustgen_uid = var.rustgen_uid
  })
}

resource "twc_server_ip" "public_ipv4" {
  count = var.enable_public_ipv4 ? 1 : 0

  source_server_id = tonumber(twc_server.rust_gen.id)
  type             = "ipv4"
}

resource "twc_firewall" "rust_gen" {
  name        = "${var.server_name}-fw"
  description = "Reserved network rule group for the Rust renderer manager"
}

resource "twc_firewall_rule" "manager_ingress" {
  for_each = var.manager_api_cidrs

  firewall_id = twc_firewall.rust_gen.id
  description = "Allow manager API from orchestrator"
  direction   = "ingress"
  protocol    = "tcp"
  port        = var.manager_api_port
  cidr        = each.value
}

resource "twc_firewall_rule" "ssh_ingress" {
  for_each = var.ssh_allowed_cidrs

  firewall_id = twc_firewall.rust_gen.id
  description = "Allow SSH from deploy/operator CIDR"
  direction   = "ingress"
  protocol    = "tcp"
  port        = 22
  cidr        = each.value
}

# Keep the cloud firewall rules documented in state. The group intentionally
# remains unlinked: Timeweb currently blocks the node's egress when this
# firewall is attached even with explicit DNS/HTTP(S) egress rules. The manager
# ingress allow-list is instead enforced by the bootstrap/deploy nftables unit.
resource "twc_firewall_rule" "egress_tcp" {
  for_each = toset(["53", "80", "443"])

  firewall_id = twc_firewall.rust_gen.id
  description = "Allow worker outbound TCP ${each.value}"
  direction   = "egress"
  protocol    = "tcp"
  port        = tonumber(each.value)
  cidr        = "0.0.0.0/0"
}

resource "twc_firewall_rule" "egress_udp_dns" {
  firewall_id = twc_firewall.rust_gen.id
  description = "Allow worker outbound DNS"
  direction   = "egress"
  protocol    = "udp"
  port        = 53
  cidr        = "0.0.0.0/0"
}
