output "instance_ip" {
  description = "Static external IP. Create a DNS A record for var.domain pointing here (not needed when using the auto nip.io domain)."
  value       = google_compute_address.this.address
}

output "domain" {
  description = "Domain the service is served on (auto-derived nip.io when var.domain is empty)."
  value       = local.effective_domain
}

output "url" {
  description = "URL the dashboard/webhooks are served on."
  value       = "https://${local.effective_domain}"
}

output "instance_name" {
  description = "Name of the created instance."
  value       = google_compute_instance.this.name
}

output "sops_kms_key" {
  description = "KMS key the SOPS secrets file is encrypted with (referenced in .sops.yaml)."
  value       = google_kms_crypto_key.sops.id
}

output "next_steps" {
  description = "What to do after apply."
  value       = <<-EOT
    1. DNS: ${var.domain != "" ? "create an A record for ${var.domain} -> ${google_compute_address.this.address}" : "none needed — using ${local.effective_domain}"}
    2. Edit secrets and re-apply to ship them:
         sops secrets.enc.yaml         # (create/edit; KMS-encrypted)
         terraform apply               # pushes ciphertext to instance metadata
    3. SSH in and watch boot:
         gcloud compute ssh ${var.name} --zone=${var.zone} --project=${var.project_id}
         sudo journalctl -u google-startup-scripts -f
         sudo docker compose -f /opt/open-swe/src/deploy/gce-simple/files/docker-compose.yml logs -f
    Caddy issues the TLS cert automatically once DNS resolves to the instance.
  EOT
}
