import { useState } from "react"
import { EyeIcon, EyeSlashIcon } from "@phosphor-icons/react"

import { IconButton } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { WorkspaceMCPOAuthUpdate } from "@/lib/api"

export function WorkspaceMCPOAuthFields({
  value,
  onChange,
  hasSavedSecret,
}: {
  value: WorkspaceMCPOAuthUpdate
  onChange: (value: WorkspaceMCPOAuthUpdate) => void
  hasSavedSecret: boolean
}) {
  const [revealed, setRevealed] = useState(false)
  return (
    <div className="space-y-3 rounded-md border p-3">
      <p className="text-xs text-muted-foreground">
        Use an OAuth application to obtain and renew access tokens
        automatically. No redirect URI is needed for client credentials.
      </p>
      <label className="block text-sm">
        Token URL
        <Input
          aria-label="Token URL"
          required
          type="url"
          placeholder="https://api.linear.app/oauth/token"
          value={value.token_url}
          onChange={(event) =>
            onChange({ ...value, token_url: event.target.value })
          }
        />
      </label>
      <label className="block text-sm">
        Client ID
        <Input
          aria-label="Client ID"
          required
          value={value.client_id}
          onChange={(event) =>
            onChange({ ...value, client_id: event.target.value })
          }
        />
      </label>
      <label className="block text-sm">
        Client secret
        <span className="flex gap-2">
          <Input
            aria-label="Client secret"
            required={!hasSavedSecret}
            type={revealed ? "text" : "password"}
            autoComplete="off"
            spellCheck={false}
            data-dd-privacy="hidden"
            placeholder={
              hasSavedSecret
                ? "Leave blank to keep saved secret"
                : "Client secret"
            }
            value={value.client_secret ?? ""}
            onChange={(event) =>
              onChange({
                ...value,
                client_secret: event.target.value || undefined,
              })
            }
          />
          <IconButton
            type="button"
            size="icon-sm"
            variant="outline"
            aria-label={revealed ? "Hide client secret" : "Show client secret"}
            aria-pressed={revealed}
            onClick={() => setRevealed(!revealed)}
          >
            {revealed ? (
              <EyeSlashIcon aria-hidden="true" />
            ) : (
              <EyeIcon aria-hidden="true" />
            )}
          </IconButton>
        </span>
      </label>
      <label className="block text-sm">
        Scopes
        <Input
          aria-label="Scopes"
          placeholder="read,write"
          value={value.scope ?? ""}
          onChange={(event) =>
            onChange({ ...value, scope: event.target.value })
          }
        />
        <span className="text-xs text-muted-foreground">
          Use the scope names and separator required by your provider.
        </span>
      </label>
      <label className="block text-sm">
        Client authentication
        <select
          aria-label="Client authentication"
          className="mt-1 block w-full rounded-md border bg-background p-2 text-sm"
          value={value.token_endpoint_auth_method ?? "client_secret_post"}
          onChange={(event) =>
            onChange({
              ...value,
              token_endpoint_auth_method: event.target
                .value as WorkspaceMCPOAuthUpdate["token_endpoint_auth_method"],
            })
          }
        >
          <option value="client_secret_post">
            Credentials in request body
          </option>
          <option value="client_secret_basic">HTTP Basic</option>
        </select>
      </label>
    </div>
  )
}
