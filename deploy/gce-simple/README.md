# Open SWE — single GCE instance

Terraform for the simplest possible Open SWE install: one GCE VM running the
backend (LangGraph in-memory server) and the dashboard UI, behind
[Caddy](https://caddyserver.com/) which terminates TLS with automatic Let's
Encrypt certificates.

This is meant for evaluation / small internal use, not high availability. The
backend uses the in-memory LangGraph server the repo ships with
(`langgraph-cli[inmem]`). That server persists checkpoints, the store, and the
run queue to a `.langgraph_api/` directory (pickled, flushed every ~10s), which
is mounted on the `langgraph_data` volume so run state survives restarts and
rebuilds. This is dev-grade persistence — pickle, single-process, no guaranteed
format stability across `langgraph-runtime-inmem` upgrades. For real durability
and horizontal scale use LangGraph Platform (Postgres + Redis); see
[../../docs/INSTALLATION.md](../../docs/INSTALLATION.md).

## What it creates

- A static external IP.
- One `e2-standard-4` Ubuntu 24.04 instance.
- Firewall rules: 80/443 open, 22 restricted to `ssh_source_ranges`.
- A dedicated service account with read access to one Secret Manager secret.
- A Secret Manager secret (`<name>-env`) holding the backend `.env`.

At boot the instance installs Docker, clones the repo at `repo_ref`, pulls the
`.env` from Secret Manager, and runs `docker compose up` for four services:
`backend` (LangGraph `:2024`), `ui-build` (one-shot `pnpm build`), `ui` (the
TanStack Start SSR node server on `:3000`), and `caddy`.

## Request routing (single domain)

Caddy reverse-proxies the backend paths and sends everything else to the UI
server (same-origin, so `/dashboard/api/*` needs no CORS):

| Path | Target |
|---|---|
| `/webhooks/*` | backend `:2024` (GitHub / Linear / Slack) |
| `/dashboard/api/*` | backend `:2024` (dashboard API + OAuth) |
| `/health` | backend `:2024` |
| everything else | UI SSR server `:3000` |

The UI is a TanStack Start SPA-mode app served by its own node server
(`.output/server/index.mjs`) rather than as static files — the static
`.output/public` build relies on a prerender shell that Vercel generates but a
headless build does not.

## Secrets (SOPS + GCP KMS)

The backend `.env` is generated on the box from a single SOPS-encrypted file,
`secrets.enc.yaml` (a flat `KEY: value` map of the env vars). It is encrypted
with a GCP KMS key — the one root of trust — so:

- The VM decrypts it at boot using its service account's KMS access. No secret
  material lives in Terraform state, instance metadata (ciphertext only), or a
  Secret Manager blob.
- You edit it with `sops secrets.enc.yaml` (decrypts in your editor, re-encrypts
  on save). `.sops.yaml` pins the KMS key; `sops_operators` grants your identity
  encrypt+decrypt.
- `secrets.enc.yaml` is gitignored here (per-deployment) but is safe to store in
  a private repo or GCS if you want it versioned.

`DOMAIN` / `ACME_EMAIL` are appended from Terraform at boot — don't put them in
the file. `DASHBOARD_API_BASE_URL` / `DASHBOARD_BASE_URL` must be `https://<domain>`.

## Usage

```bash
cd deploy/gce-simple
cp terraform.tfvars.example terraform.tfvars   # edit project_id, domain, acme_email, sops_operators
terraform init
terraform apply                                # creates the KMS key first

# author secrets, then ship them:
sops secrets.enc.yaml                          # create/edit the KEY: value map
terraform apply                                # pushes ciphertext to the VM via metadata
```

Then:

1. **DNS** — create an A record for your `domain` at `instance_ip` (skip when
   using the auto `nip.io` domain). Caddy issues the TLS cert once it resolves.
2. Point your GitHub App / Slack / Linear webhook URLs at
   `https://<domain>/webhooks/{github,slack,linear}` and the dashboard OAuth
   callback at `https://<domain>/dashboard/api/auth/callback`.

## Updating

Push new code, then re-run the startup script on the box (it re-checks-out
`repo_ref` and rebuilds):

```bash
gcloud compute ssh <name> --zone=<zone> --project=<project> \
  --command 'sudo google_metadata_script_runner startup'
```

To pick up a new `.env`, add a new secret version and re-run the same command.

## Debugging

```bash
gcloud compute ssh <name> --zone=<zone> --project=<project>
sudo journalctl -u google-startup-scripts -f
sudo docker compose -f /opt/open-swe/src/deploy/gce-simple/files/docker-compose.yml logs -f
```
