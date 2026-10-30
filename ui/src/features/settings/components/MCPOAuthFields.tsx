import { useState, type InputHTMLAttributes } from "react"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import {
  getInputContainerClasses,
  getInputElementClasses,
} from "@langchain/macaw-components/Input/inputStyles"
import { Select } from "@langchain/macaw-components/Select"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { EyeSlashIcon } from "@phosphor-icons/react/dist/ssr/EyeSlash"

import type { MCPOAuthUpdate } from "@/lib/api"

type AuthMethod = NonNullable<MCPOAuthUpdate["token_endpoint_auth_method"]>

const AUTH_METHODS: Array<{ value: AuthMethod; label: string }> = [
  { value: "client_secret_post", label: "Credentials in request body" },
  { value: "client_secret_basic", label: "HTTP Basic" },
]

/**
 * A masked field with its own labelled reveal toggle. A native input styled as
 * Macaw's: Macaw's password Input adds an unlabelled toggle of its own.
 */
export function SecretInput({
  label,
  revealed,
  onRevealedChange,
  ...input
}: {
  label: string
  revealed: boolean
  onRevealedChange: (revealed: boolean) => void
} & Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "aria-label">) {
  return (
    <div className="flex gap-space-2">
      <div
        className={getInputContainerClasses({
          size: "md",
          variant: "outlined",
          disabled: input.disabled,
        })}
      >
        <input
          aria-label={label}
          type={revealed ? "text" : "password"}
          autoComplete="off"
          spellCheck={false}
          data-dd-privacy="hidden"
          className={getInputElementClasses({ size: "md" })}
          {...input}
        />
      </div>
      <IconButton
        type="button"
        icon={revealed ? EyeSlashIcon : EyeIcon}
        label={`${revealed ? "Hide" : "Show"} ${label.toLowerCase()}`}
        aria-pressed={revealed}
        size="md"
        color="secondary"
        variant="outlined"
        onClick={() => onRevealedChange(!revealed)}
      />
    </div>
  )
}

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
    <div className="space-y-space-3 rounded-md border border-default p-space-3">
      <p className="text-xs text-secondary">
        Use an OAuth application to obtain and renew access tokens
        automatically. No redirect URI is needed for client credentials.
      </p>
      <Input
        label="Token URL"
        aria-label="Token URL"
        size="md"
        required
        type="url"
        placeholder="https://api.linear.app/oauth/token"
        value={value.token_url}
        onChange={(token_url) => onChange({ ...value, token_url })}
      />
      <Input
        label="Client ID"
        aria-label="Client ID"
        size="md"
        required
        value={value.client_id}
        onChange={(client_id) => onChange({ ...value, client_id })}
      />
      <div className="space-y-space-1">
        <span className="block text-sm font-medium text-primary">
          Client secret
        </span>
        <SecretInput
          label="Client secret"
          revealed={revealed}
          onRevealedChange={setRevealed}
          required={!hasSavedSecret}
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
      </div>
      <Input
        label="Scopes"
        aria-label="Scopes"
        size="md"
        placeholder="read,write"
        hintText="Use the scope names and separator required by your provider."
        value={value.scope ?? ""}
        onChange={(scope) => onChange({ ...value, scope })}
      />
      <div className="space-y-space-1">
        <span className="block text-sm font-medium text-primary">
          Client authentication
        </span>
        <Select
          aria-label="Client authentication"
          options={AUTH_METHODS}
          value={value.token_endpoint_auth_method ?? "client_secret_post"}
          onChange={(method) =>
            method && onChange({ ...value, token_endpoint_auth_method: method })
          }
          size="md"
        />
      </div>
    </div>
  )
}
