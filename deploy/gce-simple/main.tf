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
}

resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  service            = "secretmanager.googleapis.com"
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

resource "google_secret_manager_secret" "env" {
  secret_id = "${var.name}-env"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "env" {
  count       = var.env_secret_content == "" ? 0 : 1
  secret      = google_secret_manager_secret.env.id
  secret_data = var.env_secret_content
}

resource "google_secret_manager_secret_iam_member" "env_accessor" {
  secret_id = google_secret_manager_secret.env.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.this.email}"
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
      project_id = var.project_id
      secret_id  = google_secret_manager_secret.env.secret_id
      domain     = local.effective_domain
      acme_email = var.acme_email
      repo_url   = var.repo_url
      repo_ref   = var.repo_ref
    })
  }

  depends_on = [
    google_secret_manager_secret_iam_member.env_accessor,
    google_project_service.compute,
  ]
}
