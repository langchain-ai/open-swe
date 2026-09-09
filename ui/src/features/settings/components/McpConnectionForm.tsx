import { useState } from "react"
import type { ReactNode } from "react"
import { Eye, EyeOff, Trash2 } from "lucide-react"

import { Button, IconButton } from "@/components/ui/button"
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
  McpScope,
  McpToolInfo,
  McpTransport,
} from "@/lib/mcp"
import type { ImportedMCP } from "./McpJsonImport"

export type McpEditor =
  | {
      source: "cloud"
      record?: McpConnection
      preset?: McpPreset
      imported?: ImportedMCP
    }
  | { source: "local"; record?: LocalMcpServer }
export type McpSave =
  | { source: "cloud"; record: McpConnectionInput }
  | { source: "local"; record: LocalMcpServer }

type HeaderRow = { name: string; value: string; revealed: boolean }
type Transport = McpTransport | "stdio"

export const WORKSPACE_NAME_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/
export const WORKSPACE_NAME_MESSAGE =
  "Use a lowercase name such as incident. Dots and spaces are not allowed."

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

function parseJson(text: string, fallback: string, message: string): unknown {
  try {
    return JSON.parse(text.trim() || fallback)
  } catch {
    throw new Error(message)
  }
}

function stringMap(text: string, label: string): Record<string, string> {
  const message = `${label} must be a JSON object with string values.`
  const value = parseJson(text, "{}", message)
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.values(value).some((item) => typeof item !== "string")
  ) {
    throw new Error(message)
  }
  return value as Record<string, string>
}

function stringList(text: string): Array<string> {
  const message = "Arguments must be a JSON array of strings."
  const value = parseJson(text, "[]", message)
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string"))
    throw new Error(message)
  return value as Array<string>
}

const BEARER = /^Bearer\s+(\S+)$/i

function localBearer(headers?: Record<string, string>): string | null {
  const [entry, ...rest] = Object.entries(headers ?? {})
  if (!entry || rest.length || entry[0].toLowerCase() !== "authorization")
    return null
  return BEARER.exec(entry[1])?.[1] ?? null
}

function toRows(
  headers: Record<string, string> | undefined,
  names: Array<string> = []
): Array<HeaderRow> {
  if (headers)
    return Object.entries(headers).map(([name, value]) => ({
      name,
      value,
      revealed: false,
    }))
  return names.map((name) => ({ name, value: "", revealed: false }))
}

function headerMap(rows: Array<HeaderRow>): Record<string, string> {
  const headers: Record<string, string> = {}
  for (const row of rows) {
    const name = row.name.trim()
    if (!name || !row.value.trim())
      throw new Error("Each header needs a name and value.")
    if (
      Object.keys(headers).some(
        (key) => key.toLowerCase() === name.toLowerCase()
      )
    )
      throw new Error("Header names must be unique.")
    headers[name] = row.value
  }
  return headers
}

export function McpConnectionForm({
  editor,
  scope = "user",
  localAvailable,
  pending,
  queued = 0,
  onSave,
  onClose,
  onSkip,
  onDiscover,
  onRevealHeaders,
}: {
  editor: McpEditor
  scope?: McpScope
  localAvailable: boolean
  pending: boolean
  /** Imported connections still waiting for review after this one. */
  queued?: number
  onSave: (value: McpSave) => Promise<void>
  onClose: () => void
  onSkip?: () => void
  onDiscover?: (record: McpConnectionInput) => Promise<Array<McpToolInfo>>
  onRevealHeaders?: (id: string) => Promise<Record<string, string>>
}) {
  const workspace = scope === "workspace"
  const cloud = editor.source === "cloud" ? editor.record : undefined
  const local = editor.source === "local" ? editor.record : undefined
  const preset = editor.source === "cloud" ? editor.preset : undefined
  const imported = editor.source === "cloud" ? editor.imported : undefined
  const [source, setSource] = useState(editor.source)
  const [name, setName] = useState(
    imported?.name ?? cloud?.name ?? local?.name ?? preset?.name ?? ""
  )
  const [transport, setTransport] = useState<Transport>(
    imported?.transport ??
      cloud?.transport ??
      local?.transport ??
      "streamable_http"
  )
  const [url, setUrl] = useState(
    imported?.url ?? cloud?.url ?? local?.url ?? preset?.url ?? ""
  )
  const savedBearer = localBearer(local?.headers)
  const [auth, setAuth] = useState<McpAuthType>(
    (imported?.headers ? "headers" : undefined) ??
      cloud?.auth_type ??
      (local?.auth_type === "oauth" || local?.auth_type === "none"
        ? local.auth_type
        : undefined) ??
      preset?.auth_type ??
      (savedBearer
        ? "bearer"
        : local?.headers && Object.keys(local.headers).length
          ? "headers"
          : "none")
  )
  const [rows, setRows] = useState<Array<HeaderRow>>(() =>
    imported?.headers
      ? toRows(imported.headers)
      : local?.headers && !savedBearer
        ? toRows(local.headers)
        : toRows(undefined, cloud?.headers_configured ? cloud.header_names : [])
  )
  // Untouched rows for a configured connection keep the saved secrets.
  const [headersDirty, setHeadersDirty] = useState(!!imported?.headers)
  const [savedRevealed, setSavedRevealed] = useState(false)
  const [bearer, setBearer] = useState(savedBearer ?? "")
  const [clientId, setClientId] = useState(
    cloud?.oauth_client_id ?? local?.oauth_client_id ?? ""
  )
  const [clientSecret, setClientSecret] = useState("")
  const [authorizationServer, setAuthorizationServer] = useState(
    cloud?.oauth_authorization_server ?? ""
  )
  const [scopes, setScopes] = useState(
    cloud?.oauth_scope ?? local?.oauth_scope ?? ""
  )
  const [redirectUri, setRedirectUri] = useState(
    local?.oauth_redirect_uri ?? ""
  )
  const [method, setMethod] = useState<
    NonNullable<McpConnectionInput["oauth_token_endpoint_auth_method"]> | ""
  >(
    cloud?.oauth_token_endpoint_auth_method ??
      local?.oauth_token_endpoint_auth_method ??
      ""
  )
  const [command, setCommand] = useState(local?.command ?? "")
  const [args, setArgs] = useState(JSON.stringify(local?.args ?? []))
  const [env, setEnv] = useState(JSON.stringify(local?.env ?? {}, null, 2))
  const [passthrough, setPassthrough] = useState(
    (local?.env_passthrough ?? []).join(", ")
  )
  const [cwd, setCwd] = useState(local?.cwd ?? "")
  const [catalog, setCatalog] = useState<Array<McpToolInfo>>(cloud?.tools ?? [])
  const [allowed, setAllowed] = useState<Array<string>>(
    cloud?.allowed_tools ?? []
  )
  const [allTools, setAllTools] = useState(
    !workspace && (cloud ? cloud.allowed_tools === null : true)
  )
  const [discovering, setDiscovering] = useState(false)
  const [revealing, setRevealing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const descriptions = new Map(
    catalog.map((tool) => [tool.name, tool.description])
  )
  const toolNames = [...new Set([...descriptions.keys(), ...allowed])].sort()
  const selected = new Set(allowed)
  const sameUrl = cloud?.url === url.trim()
  const canRevealSaved =
    workspace &&
    !!cloud?.headers_configured &&
    !headersDirty &&
    !!onRevealHeaders
  const busy = pending || discovering || revealing

  const updateRows = (next: Array<HeaderRow>) => {
    setRows(next)
    setHeadersDirty(true)
    setSavedRevealed(false)
  }

  const cloudHeaders = (): Record<string, string> | undefined => {
    if (!headersDirty && cloud?.headers_configured && sameUrl) return undefined
    if (!rows.length) {
      if (cloud?.headers_configured) return {}
      throw new Error("Add at least one header.")
    }
    return headerMap(rows)
  }

  const cloudRecord = (): McpConnectionInput => {
    const trimmed = name.trim()
    if (!trimmed) throw new Error("Enter a server name.")
    if (workspace && !WORKSPACE_NAME_PATTERN.test(trimmed))
      throw new Error(WORKSPACE_NAME_MESSAGE)
    if (transport === "stdio") throw new Error("Choose a remote transport.")
    const endpoint = new URL(url)
    if (
      !["http:", "https:"].includes(endpoint.protocol) ||
      endpoint.username ||
      endpoint.password
    )
      throw new Error("Enter an HTTP(S) URL without embedded credentials.")
    const record: McpConnectionInput = {
      ...(cloud ? { id: cloud.id } : {}),
      name: trimmed,
      url: url.trim(),
      transport,
      auth_type: auth,
      enabled: cloud?.enabled ?? true,
      allowed_tools: workspace ? allowed : allTools ? null : allowed,
    }
    if (auth === "headers") {
      const headers = cloudHeaders()
      if (headers) record.headers = headers
    }
    if (auth === "bearer" && bearer) record.bearer_token = bearer
    if (auth === "oauth") {
      record.oauth_client_id = clientId
      record.oauth_authorization_server = authorizationServer.trim()
      if (clientSecret) record.oauth_client_secret = clientSecret
      record.oauth_scope = scopes
      if (method) record.oauth_token_endpoint_auth_method = method
    }
    return record
  }

  const discover = async () => {
    if (!onDiscover) return
    setError(null)
    setDiscovering(true)
    try {
      const tools = await onDiscover(cloudRecord())
      setCatalog(tools)
      if (!cloud) setAllowed(tools.map((tool) => tool.name))
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not discover tools."
      )
    } finally {
      setDiscovering(false)
    }
  }

  const toggleSaved = async () => {
    if (savedRevealed) {
      setRows(toRows(undefined, cloud?.header_names))
      setSavedRevealed(false)
      return
    }
    if (!cloud || !onRevealHeaders) return
    setError(null)
    setRevealing(true)
    try {
      const headers = await onRevealHeaders(cloud.id)
      setRows(
        Object.entries(headers).map(([key, value]) => ({
          name: key,
          value,
          revealed: true,
        }))
      )
      setSavedRevealed(true)
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not reveal headers."
      )
    } finally {
      setRevealing(false)
    }
  }

  const save = async () => {
    setError(null)
    try {
      if (source === "cloud") {
        await onSave({ source, record: cloudRecord() })
        return
      }
      if (!name.trim()) throw new Error("Enter a server name.")
      if (transport === "sse") throw new Error("Choose a supported transport.")
      if (transport === "stdio" && !command.trim())
        throw new Error("Enter a command.")
      if (transport === "streamable_http") {
        const endpoint = new URL(url)
        if (
          !["http:", "https:"].includes(endpoint.protocol) ||
          endpoint.username ||
          endpoint.password
        )
          throw new Error("Enter an HTTP(S) URL without embedded credentials.")
      }
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
                env_vars: local?.env_vars,
              }
            : {
                url: url.trim(),
                auth_type: auth === "bearer" ? "headers" : auth,
                ...(auth === "oauth"
                  ? {
                      oauth_client_id: clientId || undefined,
                      oauth_scope: scopes || undefined,
                      oauth_redirect_uri: redirectUri || undefined,
                      oauth_token_endpoint_auth_method: method || "none",
                      ...(clientSecret
                        ? { oauth_client_secret: clientSecret }
                        : {}),
                    }
                  : {}),
                headers:
                  auth === "bearer"
                    ? { Authorization: `Bearer ${bearer}` }
                    : auth === "headers"
                      ? headerMap(rows)
                      : {},
              }),
        },
      })
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
            {workspace
              ? "Shared by every authorized run in this workspace."
              : "Connect tools and data to Open SWE."}
          </SheetDescription>
        </SheetHeader>
        <form
          className="space-y-5 overflow-y-auto px-6 pb-6"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <fieldset disabled={busy} className="space-y-5">
            {queued > 0 && (
              <p className="text-xs text-muted-foreground">
                {queued} more {queued === 1 ? "connection" : "connections"} to
                review after this one.
              </p>
            )}
            <Field label="Name">
              <Input
                required
                maxLength={workspace ? 32 : 100}
                value={name}
                onChange={(event) => setName(event.target.value)}
                readOnly={!!local}
                placeholder={workspace ? "incident" : "My MCP server"}
              />
            </Field>
            {workspace && (
              <p className="text-xs text-muted-foreground">
                {WORKSPACE_NAME_MESSAGE}
              </p>
            )}
            {local && (
              <p className="text-xs text-muted-foreground">
                Local server names are identifiers. Add a new server to rename
                one.
              </p>
            )}
            <div className="grid grid-cols-2 gap-3">
              {!workspace && (
                <Field label="Source">
                  <select
                    className={selectClass}
                    value={source}
                    disabled={!!editor.record}
                    onChange={(event) => {
                      setSource(event.target.value as "cloud" | "local")
                      setTransport("streamable_http")
                    }}
                  >
                    <option value="cloud">Cloud</option>
                    <option value="local" disabled={!localAvailable}>
                      This device
                    </option>
                  </select>
                </Field>
              )}
              <Field label="Transport">
                <select
                  className={selectClass}
                  value={transport}
                  onChange={(event) =>
                    setTransport(event.target.value as Transport)
                  }
                >
                  <option value="streamable_http">Streamable HTTP</option>
                  <option value="sse" disabled={source !== "cloud"}>
                    SSE
                  </option>
                  {!workspace && (
                    <option value="stdio" disabled={source !== "local"}>
                      stdio (desktop only)
                    </option>
                  )}
                </select>
              </Field>
            </div>
            <p className="text-xs text-muted-foreground">
              {workspace
                ? "Available to every authorized coding-agent run. Only the tools you allow below can be called."
                : source === "cloud"
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
                    placeholder="MY_API_KEY, GITHUB_TOKEN"
                  />
                  <span className="font-normal text-muted-foreground">
                    Only these login-shell variables reach the server, plus
                    HOME, PATH, USER and a few other basics.
                  </span>
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
                    {!workspace && <option value="oauth">OAuth</option>}
                  </select>
                </Field>
                {auth === "bearer" && (
                  <Field label="Bearer token">
                    <Input
                      type="password"
                      autoComplete="new-password"
                      value={bearer}
                      onChange={(event) => setBearer(event.target.value)}
                      required={!cloud?.bearer_token_configured || !sameUrl}
                      placeholder={
                        cloud?.bearer_token_configured
                          ? sameUrl
                            ? "Configured · leave blank to keep"
                            : "New URL · enter the token again"
                          : "Enter token"
                      }
                    />
                  </Field>
                )}
                {auth === "headers" && (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-medium">Headers</p>
                      {canRevealSaved && (
                        <IconButton
                          type="button"
                          size="icon-sm"
                          variant="outline"
                          aria-label={
                            savedRevealed
                              ? "Hide saved headers"
                              : "Show saved headers"
                          }
                          title={
                            savedRevealed
                              ? "Hide saved headers"
                              : "Show saved headers"
                          }
                          aria-pressed={savedRevealed}
                          onClick={() => void toggleSaved()}
                        >
                          {savedRevealed ? (
                            <EyeOff aria-hidden="true" />
                          ) : (
                            <Eye aria-hidden="true" />
                          )}
                        </IconButton>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {cloud?.headers_configured && !headersDirty
                        ? "Saved values stay hidden. Edit a row to replace them, or remove every row to clear them."
                        : "Use a header such as Authorization or X-API-Key. Values are encrypted at rest."}
                    </p>
                    {rows.map((row, index) => (
                      <div className="flex items-center gap-2" key={index}>
                        <Input
                          aria-label={`Header ${index + 1} name`}
                          placeholder="Authorization"
                          value={row.name}
                          onChange={(event) =>
                            updateRows(
                              rows.map((item, i) =>
                                i === index
                                  ? { ...item, name: event.target.value }
                                  : item
                              )
                            )
                          }
                        />
                        <Input
                          aria-label={`Header ${index + 1} value`}
                          placeholder={
                            !row.value &&
                            cloud?.headers_configured &&
                            !headersDirty
                              ? "Saved"
                              : "Bearer …"
                          }
                          type={row.revealed ? "text" : "password"}
                          autoComplete="off"
                          spellCheck={false}
                          data-dd-privacy="hidden"
                          value={row.value}
                          onChange={(event) =>
                            updateRows(
                              rows.map((item, i) =>
                                i === index
                                  ? { ...item, value: event.target.value }
                                  : item
                              )
                            )
                          }
                        />
                        <IconButton
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={`${row.revealed ? "Hide" : "Show"} header ${index + 1} value`}
                          aria-pressed={row.revealed}
                          onClick={() =>
                            setRows(
                              rows.map((item, i) =>
                                i === index
                                  ? { ...item, revealed: !item.revealed }
                                  : item
                              )
                            )
                          }
                        >
                          {row.revealed ? (
                            <EyeOff aria-hidden="true" />
                          ) : (
                            <Eye aria-hidden="true" />
                          )}
                        </IconButton>
                        <IconButton
                          type="button"
                          size="icon-sm"
                          variant="ghost"
                          aria-label={`Remove header ${index + 1}`}
                          onClick={() =>
                            updateRows(rows.filter((_, i) => i !== index))
                          }
                        >
                          <Trash2 aria-hidden="true" />
                        </IconButton>
                      </div>
                    ))}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        updateRows([
                          ...rows,
                          { name: "", value: "", revealed: false },
                        ])
                      }
                    >
                      Add header
                    </Button>
                    {cloud?.headers_configured &&
                      headersDirty &&
                      !rows.length && (
                        <p className="text-xs text-muted-foreground">
                          Saving with no headers clears the saved
                          authentication.
                        </p>
                      )}
                  </div>
                )}
                {auth === "oauth" && !workspace && (
                  <>
                    <p className="text-xs text-muted-foreground">
                      {source === "cloud"
                        ? "Save the server, then select Authorize to sign in with your provider."
                        : "Save the server, then start a local run. Your browser opens for authorization when needed. OAuth credentials are stored in the OS keychain; a secure keychain is required."}
                    </p>
                    <details className="rounded-lg border border-border p-3">
                      <summary className="cursor-pointer text-xs font-medium">
                        Advanced OAuth settings
                      </summary>
                      <div className="mt-4 space-y-4">
                        <p className="text-xs text-muted-foreground">
                          {source === "cloud"
                            ? "Optional for servers supporting automatic discovery and client registration. A blank secret keeps the saved secret."
                            : "For a manually registered client, enter its client ID and registered loopback redirect URI. A blank secret keeps the saved secret for the same server and client ID."}
                        </p>
                        {source === "cloud" && (
                          <Field label="Authorization server URL (cloud only)">
                            <Input
                              type="url"
                              maxLength={2048}
                              value={authorizationServer}
                              onChange={(event) =>
                                setAuthorizationServer(event.target.value)
                              }
                              placeholder="https://auth.example.com"
                            />
                            <span className="font-normal text-muted-foreground">
                              Optional when protected-resource discovery is
                              unavailable. Enter the provider's HTTPS issuer
                              URL, not its authorize endpoint.
                            </span>
                          </Field>
                        )}
                        <Field label="Client ID">
                          <Input
                            value={clientId}
                            onChange={(event) =>
                              setClientId(event.target.value)
                            }
                            placeholder="Optional client ID"
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
                              cloud?.oauth_client_secret_configured ||
                              local?.oauth_client_secret_configured
                                ? "Configured · leave blank to keep"
                                : "Optional client secret"
                            }
                          />
                        </Field>
                        {source === "local" && (
                          <Field label="Redirect URI (manual clients)">
                            <Input
                              type="url"
                              value={redirectUri}
                              onChange={(event) =>
                                setRedirectUri(event.target.value)
                              }
                              placeholder="http://127.0.0.1:PORT/callback"
                            />
                          </Field>
                        )}
                        <Field label="Scopes">
                          <Input
                            value={scopes}
                            onChange={(event) => setScopes(event.target.value)}
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
                {source === "cloud" && onDiscover && (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs font-medium">Allowed tools</p>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!name.trim() || !url.trim()}
                        onClick={() => void discover()}
                      >
                        {discovering ? "Discovering…" : "Discover tools"}
                      </Button>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {workspace
                        ? "Only listed tools can be called. New connections preselect every discovered tool; rediscovering keeps the saved selection and leaves new tools unchecked."
                        : "Discover tools to limit this server to a subset. New tools on the server stay unchecked until you allow them."}
                    </p>
                    {!workspace && (
                      <label className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={allTools}
                          onChange={(event) =>
                            setAllTools(event.target.checked)
                          }
                        />
                        All tools
                      </label>
                    )}
                    {(workspace || !allTools) && toolNames.length > 0 && (
                      <>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="mr-auto text-xs text-muted-foreground">
                            {allowed.length} of {toolNames.length} selected
                          </span>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={allowed.length === toolNames.length}
                            onClick={() => setAllowed(toolNames)}
                          >
                            Select all
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={!allowed.length}
                            onClick={() => setAllowed([])}
                          >
                            Select none
                          </Button>
                        </div>
                        <div
                          role="group"
                          aria-label="Available tools"
                          className="max-h-64 space-y-2 overflow-y-auto overscroll-contain rounded-md border border-border p-3"
                        >
                          {toolNames.map((tool) => (
                            <label
                              key={tool}
                              className="flex items-start gap-2 text-xs"
                            >
                              <input
                                type="checkbox"
                                className="mt-0.5 shrink-0"
                                aria-label={`Allow ${tool}`}
                                checked={selected.has(tool)}
                                onChange={(event) =>
                                  setAllowed(
                                    event.target.checked
                                      ? [...allowed, tool]
                                      : allowed.filter((item) => item !== tool)
                                  )
                                }
                              />
                              <span className="min-w-0 break-words">
                                {tool}
                                {descriptions.get(tool) && (
                                  <span className="block text-muted-foreground">
                                    {descriptions.get(tool)}
                                  </span>
                                )}
                              </span>
                            </label>
                          ))}
                        </div>
                      </>
                    )}
                    {(workspace || !allTools) && !toolNames.length && (
                      <p className="text-xs text-muted-foreground">
                        No tools discovered yet.
                      </p>
                    )}
                  </div>
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
            {onSkip && (
              <Button
                type="button"
                variant="ghost"
                onClick={onSkip}
                disabled={busy}
              >
                Skip connection
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={pending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {pending ? "Saving…" : "Save server"}
            </Button>
          </div>
        </form>
      </SheetPopup>
    </Sheet>
  )
}
