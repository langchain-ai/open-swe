# Defaults, routing, and sandbox images

Robin has one global configuration for model defaults, shared behavior, instructions, and sandbox execution.
Admins manage **Defaults** at `/settings` and repository and Slack bindings under **Integrations** at `/admin/integrations`.
Personal repository and branch preferences and per-thread model choices remain separate from shared defaults.
Projects organize threads and may bind a repository; they do not select execution configuration.

## Integration bindings

Repository and Slack bindings control inbound integrations, not sandbox credentials or images.
Admins use `list_integration_bindings`, `bind_repository`, `unbind_repository`, `bind_slack_channel`, and `unbind_slack_channel`.

- `bind_repository(repo="owner/name", may_start_threads=False)` binds a repository without granting GitHub Actions permission to start threads
- Only an explicit `may_start_threads=True` authorizes federated workflow starts; repository access and OIDC verification still apply
- `bind_slack_channel(channel_id="C0123456789", mode="kitchen")` enables Kitchen (`kitchen`) or Ask (`ask`)
- Removing a binding does not delete its repository, Slack channel, threads, or image contents

The dashboard API exposes `GET /dashboard/api/routing`, `PUT` and `DELETE /dashboard/api/routing/repositories/{owner}/{name}`, and `PUT` and `DELETE /dashboard/api/routing/slack-channels/{id}`.
Bindings do not select different settings, personal credentials, or execution profiles.
Adding a repository to image preloads does not bind it or grant workflow-start permission.

## Shared defaults and images

`read_execution_defaults()` reads the configuration on an authorized private admin surface.
`configure_execution_defaults` saves shared instructions, preloads, snapshot IDs, resources, setup and update scripts, and provider/proxy configuration.
Saving configuration does not build or publish an image, change integration bindings, or recreate existing sandboxes.

Image tools operate on the one global execution image:

- `read_execution_image()` reads the current image and private build diagnostics
- `rebuild_execution_image(kind="full")` queues a build from the configured base using the setup script
- `rebuild_execution_image(kind="update")` queues an update from the current image using the update script
- `publish_execution_image()` captures this thread's sandbox without stopping it
- `snapshotctl(action="status")` and `snapshotctl(action="history")` read diagnostics and recent audited image actions; `snapshotctl(action="stop")` stops the current build

A returned `image-...` task ID means queued work, not completed publication.
Inspect or stop it with `background_task`.
Private diagnostics require a private dashboard admin thread or authenticated Slack DM.
Supported admin writes also work in verified sole-writer shared threads, returning only acknowledgements.
Publishing changes what everyone's future sandboxes boot from; it requires an explicit request.
No build or update button is provided on the Defaults page.

Existing sandboxes retain their files and creation settings.
Only an explicit recreation request replaces a thread's sandbox: `recreate_sandbox(source="defaults")` uses current global defaults, while `source="base"` starts from the empty base snapshot.
The old sandbox is preserved but becomes inaccessible from the thread after the handoff.

### Scripts and preloaded repositories

`setup_script` describes the complete reproducible build from the configured base snapshot.
A configured setup script runs in a throwaway builder for nightly full builds; no setup script means no nightly full build.
`update_script` is optional and normally synchronizes repositories and dependencies.
Updates do not retrofit an existing sandbox.
A failed build leaves the previous good image in place.

Scripts receive space-delimited preload names through `ROBIN_REPOS` and clone them under `/workspace/<repo>`.
`OPENSWE_REPOS` and `OPENSWE_WORKSPACE_REPOS` are aliases for compatibility with already-stored scripts; use `ROBIN_REPOS` in new scripts.
Preloads are an intended image inventory, not proof that an existing sandbox contains a checkout.
Do not write secrets into captured files.

### Command environment and installed tools

Snapshots preserve installed files, not a provisioning shell's exports.
Commands are non-interactive, so activating mise, Rust, or Python tools only in `.bashrc` is insufficient.
Configure persistent, nonsecret command defaults through `create_params.run_config.env_vars`.
For example, an image that runs as root can use:

```json
{
  "run_config": {
    "env_vars": {
      "PATH": "/root/.local/bin:/root/.cargo/bin:/root/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    }
  }
}
```

Use a complete literal PATH appropriate to the command user; JSON values do not expand `$PATH`, `$HOME`, or `~`.
Preserve other creation settings.
Before publishing, verify tool lookup and versions from a separate command without inline activation or exports, including tools used by commit hooks.

## GitHub access and the sandbox image

Coding sandboxes normally get a token for the whole GitHub App installation, not only bound or preloaded repositories.
Threads started by GitHub events or issue automations on public repositories record a trusted repository scope and receive a token narrowed to that repository, including on proxy refresh.
Unknown repository visibility also causes repository-scoped access.
Reviewer and review-scout sandboxes explicitly scope tokens to the repository being examined.
Member-started coding threads and global image builders normally use installation-wide access.

Bindings, private-thread visibility, and narrow tokens do not hide source already captured in an image.
Only preload repositories every admitted user is trusted to read.
Separate trust groups need separate deployments, GitHub App installations, and appropriately restricted images.

## Sandbox cloud identity and LangSmith access

Admins configure nonsecret AWS, GCP, and Azure federation profiles under **Defaults → Sandbox Proxy**.
These profiles and governed tag assignments are global sandbox creation settings, not repository-binding permissions.
No cloud keys or client secrets belong in this configuration.
Federated rules use `tag:<tag value UUID>` scope and the deployment's LangSmith workspace identity.
Cloud trust must match the issuer and exact subject `ws:<LangSmith workspace UUID>:sandbox:tag:<tag value UUID>`.
Robin validates UUIDs and scope membership; LangSmith validates tag ownership and assignment permissions.
Saving a profile does not prove the cloud will accept it.

Cloud permissions apply to every matching guest request, not only filesystem mounts.
Grant only necessary permissions.
The sandbox host proxy exchanges and applies credentials; they are not stored in the image or passed to the guest.
Image builders use the same global creation settings.
Changing saved settings does not revoke authority in existing sandboxes; cloud trust or LangSmith tag revocation is separate.

**LangSmith Access** lists the permissions code in new sandboxes may use, such as `projects:read` and `datasets:read`.
The deployment's sandbox-creating LangSmith key delegates these permissions, capped by its own grants; no key enters the sandbox.
Tenant administration (`workspaces:manage*`, `organization:manage*`, `organization:pats:*`) is refused.
LangSmith and Slack workspace membership, IDs, and credentials remain service-level concepts, independent of Robin's routing bindings.

## Threads and integrations

Public threads remain visible to signed-in users, and private threads retain their owner and admin access checks.
The thread-sharing endpoint is `/dashboard/api/threads/{id}/share`.
The `workspace` thread-visibility value names shared visibility, not an execution environment.
Private threads load their owner's personal LangSmith Managed Tools kit; other threads use Robin's bot kit.
Global defaults and bindings do not grant access to another person's credentials.
