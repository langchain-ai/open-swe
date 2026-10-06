import { useState } from "react"
import { EyeIcon, EyeSlashIcon } from "@phosphor-icons/react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import type { MCPOAuthUpdate } from "@/lib/api"

export function MCPOAuthFields({
  value,
  onChange,
  hasSavedSecret,
}: {
  value: MCPOAuthUpdate
  onChange: (value: MCPOAuthUpdate) => void
  hasSavedSecret: boolean
}) {
  const [revealed, setRevealed] = useState(false)
  return (
    <div className="space-y-3 rounded-badge border p-3">
      <p className="text-meta text-ink-subtle">
        Use an OAuth application to obtain and renew access tokens
        automatically. No redirect URI is needed for client credentials.
      </p>
      <label className="block text-body">
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
      <label className="block text-body">
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
      <label className="block text-body">
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
          <Button
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
          </Button>
        </span>
      </label>
      <label className="block text-body">
        Scopes
        <Input
          aria-label="Scopes"
          placeholder="read,write"
          value={value.scope ?? ""}
          onChange={(event) =>
            onChange({ ...value, scope: event.target.value })
          }
        />
        <span className="text-meta text-ink-subtle">
          Use the scope names and separator required by your provider.
        </span>
      </label>
      <label className="block text-body">
        Client authentication
        <select
          aria-label="Client authentication"
          className="mt-1 block w-full rounded-badge border bg-canvas p-2 text-body"
          value={value.token_endpoint_auth_method ?? "client_secret_post"}
          onChange={(event) =>
            onChange({
              ...value,
              token_endpoint_auth_method: event.target
                .value as MCPOAuthUpdate["token_endpoint_auth_method"],
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
