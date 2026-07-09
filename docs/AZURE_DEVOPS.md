# Azure DevOps Git (MVP)

Optional Azure DevOps support alongside the default GitHub flow. GitHub remains the default when `repo.scm_provider` is omitted.

## Scope (this PR)

- Pluggable `PullRequestClient` (`github` default, `azure_devops` opt-in)
- Clone Azure Repos into the sandbox with `AZURE_DEVOPS_PAT` or Entra service principal
- Create draft pull requests via Azure DevOps REST (`open_pull_request` tool)
- Service Hook receiver: `POST /webhooks/azure-devops` (work item commented + PR comment)

Not included yet: multi-project JSON maps, run lifecycle comments, ADO-specific agent tools.

## Environment

| Variable | Purpose |
|----------|---------|
| `AZURE_DEVOPS_PAT` | PAT with **Code** (read/write) and **Pull Request** scopes (optional if Entra is configured) |
| `AZURE_DEVOPS_USE_ENTRA_IDENTITY` | Set to `1` to use Entra service principal instead of PAT |
| `AZURE_TENANT_ID` | Entra tenant ID |
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_CLIENT_CERTIFICATE_PATH` | Path to client certificate PEM/PFX (preferred in production) |
| `AZURE_CLIENT_CERTIFICATE_PASSWORD` | Optional certificate password |
| `AZURE_CLIENT_SECRET` | Alternative to certificate |
| `AZURE_DEVOPS_AAD_SCOPE` | Optional token scope (default `https://app.vssps.visualstudio.com/.default`) |
| `AZURE_DEVOPS_WEBHOOK_SECRET` | Shared secret for Service Hook HTTP header |
| `AZURE_DEVOPS_WEBHOOK_SECRET_HEADER` | Header name (default `X-Azure-DevOps-Webhook-Secret`) |
| `AZURE_DEVOPS_REPO` | Git repo name for webhook routing (one repo per deployment in MVP) |

### Credential resolution order

1. `AZURE_DEVOPS_PAT` environment variable (if set, always wins)
2. `configurable.azure_devops_pat` on the run
3. Entra service principal when `AZURE_DEVOPS_USE_ENTRA_IDENTITY=1` and `AZURE_*` vars are set

For production, prefer Entra with a certificate-backed app registration and leave `AZURE_DEVOPS_PAT` empty.

## Repository config

```python
{
    "scm_provider": "azure_devops",  # omit or "github" for GitHub (default)
    "owner": "my-org",               # Azure DevOps organization
    "project": "MyProject",
    "name": "my-repo",
}
```

## Service Hook

1. Azure DevOps → **Project settings** → **Service hooks** → create subscription.
2. Events: **Work item commented** and/or **Pull request commented on**.
3. Action: **Web Hooks** → URL `https://<host>/webhooks/azure-devops`.
4. HTTP header: `X-Azure-DevOps-Webhook-Secret: <same as AZURE_DEVOPS_WEBHOOK_SECRET>`.
5. Comment on a work item or PR with `@openswe` to trigger a run.

Verify endpoint: `GET /webhooks/azure-devops`.
