locals {
  labels = merge({
    app        = "open-swe"
    managed_by = "terraform"
  }, var.labels)

  tags = ["${var.name}-web", "${var.name}-ssh"]

  # Auto-derive an <ip>.nip.io domain when none is given, so a smoke test needs
  # no DNS setup. nip.io resolves <ip>.nip.io -> <ip>, which is enough for Caddy
  # to obtain a Let's Encrypt cert.
  effective_domain = var.domain != "" ? var.domain : "${google_compute_address.this.address}.nip.io"

  # SOPS-encrypted secrets, shipped to the VM via instance metadata (ciphertext,
  # decryptable only with the KMS key). Empty on the first apply, before the file
  # exists; the instance waits for it (see startup script).
  sops_secrets_file = "${path.module}/secrets.enc.yaml"
  sops_secrets      = fileexists(local.sops_secrets_file) ? file(local.sops_secrets_file) : ""
}

resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "kms" {
  service            = "cloudkms.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_address" "this" {
  name   = "${var.name}-ip"
  region = var.region

  depends_on = [google_project_service.compute]
}

resource "google_service_account" "this" {
  account_id   = "${var.name}-vm"
  display_name = "Open SWE single-instance VM"
}

resource "google_kms_key_ring" "sops" {
  name       = var.kms_keyring_name
  location   = "global"
  depends_on = [google_project_service.kms]
}

resource "google_kms_crypto_key" "sops" {
  name     = var.kms_key_name
  key_ring = google_kms_key_ring.sops.id
}

# VM decrypts secrets at boot; operators encrypt+decrypt to edit them with sops.
resource "google_kms_crypto_key_iam_member" "vm_decrypter" {
  crypto_key_id = google_kms_crypto_key.sops.id
  role          = "roles/cloudkms.cryptoKeyDecrypter"
  member        = "serviceAccount:${google_service_account.this.email}"
}

resource "google_kms_crypto_key_iam_member" "operators" {
  for_each      = toset(var.sops_operators)
  crypto_key_id = google_kms_crypto_key.sops.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value
}

resource "google_compute_firewall" "web" {
  name    = "${var.name}-allow-web"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["${var.name}-web"]

  depends_on = [google_project_service.compute]
}

resource "google_compute_firewall" "ssh" {
  name    = "${var.name}-allow-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_source_ranges
  target_tags   = ["${var.name}-ssh"]

  depends_on = [google_project_service.compute]
}

resource "google_compute_instance" "this" {
  name         = var.name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = local.tags
  labels       = local.labels

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.this.address
    }
  }

  service_account {
    email  = google_service_account.this.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    startup-script = templatefile("${path.module}/files/startup.sh.tftpl", {
      domain     = local.effective_domain
      acme_email = var.acme_email
      repo_url   = var.repo_url
      repo_ref   = var.repo_ref
    })
    # SOPS ciphertext; the startup script reads this key and decrypts it via KMS.
    sops-secrets = local.sops_secrets
  }

  depends_on = [
    google_kms_crypto_key_iam_member.vm_decrypter,
    google_project_service.compute,
  ]
}
