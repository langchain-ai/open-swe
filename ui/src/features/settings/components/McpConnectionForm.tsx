import { useState } from "react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Sheet,
  SheetPopup,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import type {
  LocalMcpServer,
  McpAuthType,
  McpConnection,
  McpConnectionInput,
  McpPreset,
} from "@/lib/mcp"

export type McpEditor =
  | { source: "cloud"; record?: McpConnection; preset?: McpPreset }
  | { source: "local"; record?: LocalMcpServer }
export type McpSave =
  | { source: "cloud"; record: McpConnectionInput }
  | { source: "local"; record: LocalMcpServer }

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-2 text-xs font-medium">
      {label}
      {children}
    </label>
  )
}

const selectClass =
  "h-8 w-full rounded-md border border-input bg-background px-2 text-xs"

function stringMap(text: string, label: string): Record<string, string> {
  const value: unknown = JSON.parse(text || "{}")
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.values(value).some((item) => typeof item !== "string")
  ) {
    throw new Error(`${label} must be a JSON object with string values.`)
  }
  return value as Record<string, string>
}

function stringList(text: string): Array<string> {
  const value: unknown = JSON.parse(text || "[]")
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string"))
    throw new Error("Arguments must be a JSON array of strings.")
  return value as Array<string>
}

export function McpConnectionForm({
  editor,
  localAvailable,
  pending,
  onSave,
  onClose,
}: {
  editor: McpEditor
  localAvailable: boolean
  pending: boolean
  onSave: (value: McpSave) => Promise<void>
  onClose: () => void
}) {
  const cloud = editor.source === "cloud" ? editor.record : undefined
  const local = editor.source === "local" ? editor.record : undefined
  const preset = editor.source === "cloud" ? editor.preset : undefined
  const [source, setSource] = useState(editor.source)
  const [name, setName] = useState(
    cloud?.name ?? local?.name ?? preset?.name ?? ""
  )
  const [transport, setTransport] = useState<LocalMcpServer["transport"]>(
    local?.transport ?? "streamable_http"
  )
  const [url, setUrl] = useState(cloud?.url ?? local?.url ?? preset?.url ?? "")
  const [auth, setAuth] = useState<McpAuthType>(
    cloud?.auth_type ??
      preset?.auth_type ??
      (local?.headers && Object.keys(local.headers).length ? "headers" : "none")
  )
  const [headers, setHeaders] = useState(
    local?.headers ? JSON.stringify(local.headers, null, 2) : ""
  )
  const [bearer, setBearer] = useState("")
  const [clientId, setClientId] = useState("")
  const [clientSecret, setClientSecret] = useState("")
  const [scope, setScope] = useState("")
  const [method, setMethod] = useState<
    NonNullable<McpConnectionInput["oauth_token_endpoint_auth_method"]> | ""
  >("")
  const [command, setCommand] = useState(local?.command ?? "")
  const [args, setArgs] = useState(JSON.stringify(local?.args ?? []))
  const [env, setEnv] = useState(JSON.stringify(local?.env ?? {}, null, 2))
  const [passthrough, setPassthrough] = useState(
    (local?.env_passthrough ?? []).join(", ")
  )
  const [cwd, setCwd] = useState(local?.cwd ?? "")
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setError(null)
    try {
      if (!name.trim()) throw new Error("Enter a server name.")
      if (transport === "streamable_http") {
        const endpoint = new URL(url)
        if (
          !["http:", "https:"].includes(endpoint.protocol) ||
          endpoint.username ||
          endpoint.password
        )
          throw new Error("Enter an HTTP(S) URL without embedded credentials.")
      }
      if (source === "cloud") {
        const record: McpConnectionInput = {
          ...(cloud ? { id: cloud.id } : {}),
          name: name.trim(),
          url: url.trim(),
          auth_type: auth,
          enabled: cloud?.enabled ?? true,
        }
        if (auth === "headers" && headers.trim())
          record.headers = stringMap(headers, "Headers")
        if (auth === "bearer" && bearer) record.bearer_token = bearer
        if (auth === "oauth") {
          if (clientId) record.oauth_client_id = clientId
          if (clientSecret) record.oauth_client_secret = clientSecret
          if (scope) record.oauth_scope = scope
          if (method) record.oauth_token_endpoint_auth_method = method
        }
        await onSave({ source, record })
      } else {
        if (transport === "stdio" && !command.trim())
          throw new Error("Enter a command.")
        if (transport === "streamable_http" && auth === "oauth")
          throw new Error(
            "Save OAuth connections to Cloud to authorize them securely."
          )
        await onSave({
          source,
          record: {
            name: name.trim(),
            transport,
            enabled: local?.enabled ?? true,
            ...(transport === "stdio"
              ? {
                  command: command.trim(),
                  args: stringList(args),
                  env: stringMap(env, "Environment"),
                  env_passthrough: passthrough.split(/[\s,]+/).filter(Boolean),
                  cwd: cwd.trim() || undefined,
                }
              : {
                  url: url.trim(),
                  headers:
                    auth === "bearer"
                      ? { Authorization: `Bearer ${bearer}` }
                      : auth === "headers"
                        ? stringMap(headers, "Headers")
                        : {},
                }),
          },
        })
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not save server."
      )
    }
  }

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <SheetPopup showCloseButton={!pending} className="sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>
            {editor.record ? "Edit MCP server" : "Add MCP server"}
          </SheetTitle>
          <SheetDescription>
            Connect tools and data to Open SWE.
          </SheetDescription>
        </SheetHeader>
        <form
          className="space-y-5 overflow-y-auto px-6 pb-6"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <fieldset disabled={pending} className="space-y-5">
            <Field label="Name">
              <Input
                required
                maxLength={100}
                value={name}
                onChange={(event) => setName(event.target.value)}
                readOnly={!!local}
                placeholder="My MCP server"
              />
            </Field>
            {local && (
              <p className="text-xs text-muted-foreground">
                Local server names are identifiers. Add a new server to rename
                one.
              </p>
            )}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Source">
                <select
                  className={selectClass}
                  value={source}
                  disabled={!!editor.record}
                  onChange={(event) => {
                    const next = event.target.value as "cloud" | "local"
                    setSource(next)
                    if (next === "cloud") setTransport("streamable_http")
                    if (next === "local" && auth === "oauth") setAuth("none")
                  }}
                >
                  <option value="cloud">Cloud</option>
                  <option value="local" disabled={!localAvailable}>
                    This device
                  </option>
                </select>
              </Field>
              <Field label="Type">
                <select
                  className={selectClass}
                  value={transport}
                  onChange={(event) =>
                    setTransport(
                      event.target.value as LocalMcpServer["transport"]
                    )
                  }
                >
                  <option value="streamable_http">Streamable HTTP</option>
                  <option value="stdio" disabled={source !== "local"}>
                    stdio (desktop only)
                  </option>
                </select>
              </Field>
            </div>
            <p className="text-xs text-muted-foreground">
              {source === "cloud"
                ? "Available to your cloud and desktop runs. Enabling or disabling applies everywhere for your account."
                : "Stored on this device. A local server with the same name overrides the cloud server on this device."}
            </p>
            {transport === "stdio" ? (
              <>
                <Field label="Command">
                  <Input
                    required
                    value={command}
                    onChange={(event) => setCommand(event.target.value)}
                    placeholder="npx"
                  />
                </Field>
                <Field label="Arguments (JSON array)">
                  <Textarea
                    value={args}
                    onChange={(event) => setArgs(event.target.value)}
                    placeholder={
                      '["-y", "@modelcontextprotocol/server-filesystem", "/path"]'
                    }
                  />
                </Field>
                <Field label="Environment variables (JSON object)">
                  <Textarea
                    value={env}
                    onChange={(event) => setEnv(event.target.value)}
                    spellCheck={false}
                  />
                </Field>
                <Field label="Environment passthrough">
                  <Input
                    value={passthrough}
                    onChange={(event) => setPassthrough(event.target.value)}
                    placeholder="HOME, PATH, MY_API_KEY"
                  />
                </Field>
                <Field label="Working directory (optional)">
                  <Input
                    value={cwd}
                    onChange={(event) => setCwd(event.target.value)}
                    placeholder="/path/to/project"
                  />
                </Field>
                <p className="text-xs text-muted-foreground">
                  Only run commands from servers you trust. They execute on this
                  device with your permissions.
                </p>
              </>
            ) : (
              <>
                <Field label="Server URL">
                  <Input
                    required
                    type="url"
                    maxLength={2048}
                    value={url}
                    onChange={(event) => setUrl(event.target.value)}
                    placeholder="https://example.com/mcp"
                  />
                </Field>
                <Field label="Authentication">
                  <select
                    className={selectClass}
                    value={auth}
                    onChange={(event) =>
                      setAuth(event.target.value as McpAuthType)
                    }
                  >
                    <option value="none">None</option>
                    <option value="bearer">Bearer token</option>
                    <option value="headers">Custom headers</option>
                    <option value="oauth" disabled={source === "local"}>
                      OAuth (Cloud)
                    </option>
                  </select>
                </Field>
                {auth === "bearer" && (
                  <Field label="Bearer token">
                    <Input
                      type="password"
                      autoComplete="new-password"
                      value={bearer}
                      onChange={(event) => setBearer(event.target.value)}
                      required={
                        !cloud?.bearer_token_configured || cloud.url !== url
                      }
                      placeholder={
                        cloud?.bearer_token_configured
                          ? "Configured · leave blank to keep"
                          : "Enter token"
                      }
                    />
                  </Field>
                )}
                {auth === "headers" && (
                  <Field label="Headers (JSON object)">
                    <Textarea
                      value={headers}
                      onChange={(event) => setHeaders(event.target.value)}
                      spellCheck={false}
                      required={!cloud?.headers_configured || cloud.url !== url}
                      placeholder={
                        cloud?.headers_configured
                          ? "Configured · leave blank to keep; {} to clear"
                          : '{"X-API-Key": "…"}'
                      }
                    />
                  </Field>
                )}
                {auth === "oauth" && (
                  <>
                    <p className="text-xs text-muted-foreground">
                      Save the server, then select Authorize to sign in with
                      your provider.
                    </p>
                    <details className="rounded-lg border border-border p-3">
                      <summary className="cursor-pointer text-xs font-medium">
                        Advanced OAuth settings
                      </summary>
                      <div className="mt-4 space-y-4">
                        <p className="text-xs text-muted-foreground">
                          Optional for servers supporting automatic client
                          registration. Blank fields preserve existing values
                          when editing.
                        </p>
                        <Field label="Client ID">
                          <Input
                            value={clientId}
                            onChange={(event) =>
                              setClientId(event.target.value)
                            }
                            placeholder={
                              cloud?.oauth_client_configured
                                ? "Configured · leave blank to keep"
                                : "Optional client ID"
                            }
                          />
                        </Field>
                        <Field label="Client secret">
                          <Input
                            type="password"
                            autoComplete="new-password"
                            value={clientSecret}
                            onChange={(event) =>
                              setClientSecret(event.target.value)
                            }
                            placeholder={
                              cloud?.oauth_client_secret_configured
                                ? "Configured · leave blank to keep"
                                : "Optional client secret"
                            }
                          />
                        </Field>
                        <Field label="Scopes">
                          <Input
                            value={scope}
                            onChange={(event) => setScope(event.target.value)}
                            placeholder="Space-separated scopes"
                          />
                        </Field>
                        <Field label="Token endpoint authentication">
                          <select
                            className={selectClass}
                            value={method}
                            onChange={(event) =>
                              setMethod(event.target.value as typeof method)
                            }
                          >
                            <option value="">
                              {cloud ? "Keep existing" : "Automatic (none)"}
                            </option>
                            <option value="none">None (public client)</option>
                            <option value="client_secret_basic">
                              Client secret basic
                            </option>
                            <option value="client_secret_post">
                              Client secret post
                            </option>
                          </select>
                        </Field>
                      </div>
                    </details>
                  </>
                )}
              </>
            )}
          </fieldset>
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2 border-t border-border pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={pending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Saving…" : "Save server"}
            </Button>
          </div>
        </form>
      </SheetPopup>
    </Sheet>
  )
}
