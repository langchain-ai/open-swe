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
`.env` from Secret Manager, and runs `docker compose up` for three services:
`backend` (LangGraph `:2024`), `ui-build` (one-shot `pnpm build`), and `caddy`.

## Request routing (single domain)

Caddy serves the built UI as static files and reverse-proxies the backend paths,
mirroring the same-origin setup in `ui/vercel.json`:

| Path | Target |
|---|---|
| `/webhooks/*` | backend `:2024` (GitHub / Linear / Slack) |
| `/dashboard/api/*` | backend `:2024` (dashboard API + OAuth) |
| `/health` | backend `:2024` |
| everything else | static UI (`_shell.html` SPA fallback) |

## Usage

```bash
cd deploy/gce-simple
cp terraform.tfvars.example terraform.tfvars   # edit project_id, domain, acme_email
terraform init
terraform apply
```

Then, using the outputs:

1. **DNS** — create an A record for your `domain` pointing at `instance_ip`.
   Caddy can only issue the TLS cert once the domain resolves to the instance.
2. **Backend env** — fill in `files/env.example`, save it as `.env`, and load it:
   ```bash
   gcloud secrets versions add "$(terraform output -raw env_secret_name)" \
     --data-file=.env --project=<project>
   ```
   (Or set `env_secret_content` in `terraform.tfvars` to have Terraform seed it —
   at the cost of the value landing in Terraform state.)
3. Point your GitHub App / Slack / Linear webhook URLs at
   `https://<domain>/webhooks/{github,slack,linear}` and the dashboard OAuth
   callback at `https://<domain>/dashboard/api/auth/callback`.

The `.env` must set `DASHBOARD_API_BASE_URL` / `DASHBOARD_BASE_URL` to
`https://<domain>`; `DOMAIN` and `ACME_EMAIL` are appended automatically from
Terraform at boot.

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
