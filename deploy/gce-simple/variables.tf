variable "project_id" {
  type        = string
  description = "GCP project to deploy the instance into."
}

variable "region" {
  type        = string
  description = "GCP region for the static IP."
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "GCP zone for the instance."
  default     = "us-central1-a"
}

variable "name" {
  type        = string
  description = "Base name for all resources."
  default     = "open-swe"
}

variable "machine_type" {
  type        = string
  description = "Instance machine type. Needs enough RAM to build the UI and run the backend."
  default     = "e2-standard-4"
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Boot disk size in GB."
  default     = 50
}

variable "domain" {
  type        = string
  description = "Domain the service is served on. Caddy obtains a Let's Encrypt cert for it, so an A record for this domain must point at the instance IP before TLS can be issued. Leave empty to auto-derive a <static-ip>.nip.io domain (no DNS setup needed — handy for smoke tests)."
  default     = ""
}

variable "acme_email" {
  type        = string
  description = "Contact email for Let's Encrypt / ACME registration."
}

variable "ssh_source_ranges" {
  type        = list(string)
  description = "CIDR ranges allowed to reach SSH (22). Restrict this in production."
  default     = ["0.0.0.0/0"]
}

variable "repo_url" {
  type        = string
  description = "Git URL of the Open SWE repo cloned onto the instance at boot."
  default     = "https://github.com/langchain-ai/open-swe.git"
}

variable "repo_ref" {
  type        = string
  description = "Git ref (branch, tag, or SHA) to check out."
  default     = "main"
}

variable "kms_keyring_name" {
  type        = string
  description = "Name of the KMS key ring holding the SOPS key (created if absent)."
  default     = "sops-ring"
}

variable "kms_key_name" {
  type        = string
  description = "Name of the SOPS KMS crypto key."
  default     = "sops-key"
}

variable "sops_operators" {
  type        = list(string)
  description = "IAM members granted encrypt+decrypt on the SOPS key so they can edit secrets locally with `sops` (e.g. [\"user:you@example.com\"])."
  default     = []
}

variable "labels" {
  type        = map(string)
  description = "Extra labels applied to created resources."
  default     = {}
}
