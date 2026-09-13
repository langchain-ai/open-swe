# Environment auth proxy

Workspace admins can configure credentials directly on an environment in
**Settings → Environments → Authentication proxy**. Each entry specifies an exact DNS
hostname, an HTTP header, an authentication method, and a credential.

- **Bearer token** injects `Bearer <credential>` into the selected header,
  normally `Authorization`.
- **API key** injects the credential directly, for example into `X-API-Key`.

Use a hostname such as `api.staging.example.com`, without a scheme, path, port,
or wildcard. Multiple headers for the same hostname are supported. Destinations
already matched by a managed GitHub/Stagehand rule, an AWS/GCP authentication
rule, a custom proxy routing rule, or an authorization callback cannot also
have environment auth: overlapping rules could bypass the intended
authentication. With an AWS authentication rule, environment auth excludes
all `*.amazonaws.com` hosts.

Credentials are encrypted using the existing `TOKEN_ENCRYPTION_KEY` setup and
stored separately from the environment definition. The editor shows only
whether a credential is configured. Leaving an existing credential input blank
keeps it; entering a new value rotates it; removing an entry removes its stored
credential. Credentials belong to the environment, so anyone allowed to run in
that environment can make authenticated requests to its configured hosts.

Open SWE resolves credentials on the server and supplies them as opaque proxy
headers. It does not write them to the sandbox filesystem, environment
variables, snapshots, prompts, or thread metadata. Clients that require a local
API-key variable can use a non-sensitive placeholder; the proxy supplies the
real header on outbound requests. Do not put credentials in setup scripts,
prompts, additional create parameters, or chat messages.

Rules apply to new sandboxes and environment snapshot builders before scripts
run. Existing sandboxes receive current rules and rotated credentials at the
next agent run or scheduled GitHub proxy-token refresh. Saving settings does
not immediately change a running or idle sandbox. Removing an environment
also removes its stored auth rules; its existing sandboxes lose those injected
headers on their next proxy refresh.

This feature currently uses the LangSmith sandbox provider. A credential lookup
or decryption failure prevents proxy configuration and fails sandbox preparation,
rather than starting an agent with incomplete authentication.

The admin API is `GET` / `PUT /dashboard/api/environments/{slug}/auth-proxy` (under the
deployment's dashboard mount). `PUT` replaces the complete rule list. Keep each
rule's stable `id` and omit `credential` to retain the saved value. Responses
contain `has_credential` instead of credential values. An empty list clears all
rules. Ordinary environment definitions and agent tools never return credentials.
