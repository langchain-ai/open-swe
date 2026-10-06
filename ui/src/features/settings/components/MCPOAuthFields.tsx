import { useState } from "react"

import {
  FormField,
  FormSection,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@langchain/gtm-platform-design-system/ui/select"

import { Eye, EyeOff } from "@/components/glyphs"
import type { MCPOAuthUpdate } from "@/lib/api"

type ClientAuthMethod = NonNullable<
  MCPOAuthUpdate["token_endpoint_auth_method"]
>

const CLIENT_AUTH_METHODS: Array<{ value: ClientAuthMethod; label: string }> = [
  { value: "client_secret_post", label: "Credentials in request body" },
  { value: "client_secret_basic", label: "HTTP Basic" },
]

function isClientAuthMethod(value: unknown): value is ClientAuthMethod {
  return CLIENT_AUTH_METHODS.some((method) => method.value === value)
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
    <FormSection
      title="OAuth client"
      description="Use an OAuth application to obtain and renew access tokens automatically. No redirect URI is needed for client credentials."
    >
      <FormField
        label="Token URL"
        required
        control={
          <Input
            required
            type="url"
            placeholder="https://api.linear.app/oauth/token"
            value={value.token_url}
            onChange={(event) =>
              onChange({ ...value, token_url: event.target.value })
            }
          />
        }
      />
      <FormField
        label="Client ID"
        required
        control={
          <Input
            required
            value={value.client_id}
            onChange={(event) =>
              onChange({ ...value, client_id: event.target.value })
            }
          />
        }
      />
      <Inline gap="sm" align="end">
        <Box className="min-w-0 flex-1">
          <FormField
            label="Client secret"
            required={!hasSavedSecret}
            control={
              <Input
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
            }
          />
        </Box>
        <Button
          type="button"
          size="icon"
          variant="outline"
          aria-label={revealed ? "Hide client secret" : "Show client secret"}
          aria-pressed={revealed}
          onClick={() => setRevealed(!revealed)}
        >
          <Icon icon={revealed ? EyeOff : Eye} size="md" />
        </Button>
      </Inline>
      <FormField
        label="Scopes"
        help="Use the scope names and separator required by your provider."
        control={
          <Input
            placeholder="read,write"
            value={value.scope ?? ""}
            onChange={(event) =>
              onChange({ ...value, scope: event.target.value })
            }
          />
        }
      />
      <FormField
        label="Client authentication"
        control={
          <Select
            items={CLIENT_AUTH_METHODS}
            value={value.token_endpoint_auth_method ?? "client_secret_post"}
            onValueChange={(method) => {
              if (isClientAuthMethod(method))
                onChange({ ...value, token_endpoint_auth_method: method })
            }}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CLIENT_AUTH_METHODS.map((method) => (
                <SelectItem key={method.value} value={method.value}>
                  {method.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
    </FormSection>
  )
}
