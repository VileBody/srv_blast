variable "twc_token" {
  description = "Optional explicit Timeweb Cloud API token. Normal deployments use TWC_TOKEN directly."
  type        = string
  sensitive   = true
  default     = ""
}

variable "project_id" {
  description = "Optional Timeweb project ID."
  type        = number
  default     = null
}

variable "os_id" {
  description = "Linux OS image ID from Timeweb, not the legacy Windows custom image ID."
  type        = number
}

variable "preset_id" {
  description = "Timeweb preset. Start with the measured 8 vCPU / 16 GiB profile, then tune from production telemetry."
  type        = number
  default     = 4807
}

variable "availability_zone" {
  description = "Timeweb availability zone, for example msk-1."
  type        = string
}

variable "server_name" {
  description = "Dedicated Rust renderer node name."
  type        = string
  default     = "blast-rust-gen-1"
}

variable "enable_public_ipv4" {
  description = "Whether to attach a public IPv4. Keep true until a private orchestrator path is configured."
  type        = bool
  default     = true
}

variable "ssh_key_ids" {
  description = "Timeweb SSH key IDs allowed for initial node administration."
  type        = list(number)
  default     = []
}

variable "manager_api_port" {
  description = "Rust manager API port."
  type        = number
  default     = 8090
}

variable "manager_api_cidrs" {
  description = "Only orchestrator ingress CIDRs. 0.0.0.0/0 is rejected."
  type        = set(string)

  validation {
    condition     = length(var.manager_api_cidrs) > 0 && alltrue([for cidr in var.manager_api_cidrs : can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"])
    error_message = "manager_api_cidrs must contain one or more restricted CIDRs; public internet ingress is not allowed."
  }
}

variable "ssh_allowed_cidrs" {
  description = "Restricted operator/deploy-runner CIDRs for SSH."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.ssh_allowed_cidrs : can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"])
    error_message = "ssh_allowed_cidrs must use valid, non-public CIDRs."
  }
}

variable "rustgen_uid" {
  description = "Stable service UID used by rootless Podman and the user systemd unit."
  type        = number
  default     = 1001
}
