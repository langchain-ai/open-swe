import { useId, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { EyeSlashIcon } from "@phosphor-icons/react/dist/ssr/EyeSlash"

import { SettingsSection } from "@/components/AppShell"
import { api, DEFAULT_WORKSPACE_SLUG } from "@/lib/api"
import type { MCPConnection, MCPConnectionUpdate } from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { MCPImport } from "./MCPImport"
import type { ImportedMCP } from "./MCPImport"
import { MCPOAuthFields, SecretInput } from "./MCPOAuthFields"

type Header = { name: string; value: string; revealed?: boolean }
type Draft = Omit<MCPConnectionUpdate, "headers"> & { existing: boolean }
type Catalog = { name: string; description: string }[]
type AuthMode = "headers" | "oauth"

const TRANSPORTS: Array<{ value: MCPConnection["transport"]; label: string }> =
  [
    { value: "streamable_http", label: "Streamable HTTP" },
    { value: "sse", label: "SSE" },
  ]

const AUTH_MODES: Array<{ value: AuthMode; label: string }> = [
  { value: "headers", label: "Headers / API key" },
  { value: "oauth", label: "OAuth client credentials" },
]

function editableValues(connection?: MCPConnectionUpdate): string {
  const oauth = connection?.oauth
  return JSON.stringify([
    connection?.name ?? "",
    connection?.url ?? "",
    connection?.transport ?? "streamable_http",
    connection?.enabled ?? true,
    [...(connection?.allowed_tools ?? [])].sort(),
    oauth
      ? [
          oauth.token_url,
          oauth.client_id,
          oauth.scope ?? "",
          oauth.token_endpoint_auth_method ?? "client_secret_post",
          oauth.client_secret ?? "",
        ]
      : null,
  ])
}

export type MCPScope = "instance" | "workspace" | "user"

type MCPScopeConfig = {
  description: string
  queryKey: string[]
  list: () => Promise<MCPConnection[]>
  save: (body: MCPConnectionUpdate) => Promise<MCPConnection>
  remove: (name: string) => Promise<void>
  revealHeaders: (name: string) => Promise<Record<string, string>>
  discover: (body: MCPConnectionUpdate) => Promise<Catalog>
}

function scopeConfig(scope: MCPScope, workspace: string): MCPScopeConfig {
  const scopes: Record<MCPScope, MCPScopeConfig> = {
    instance: {
      description:
        "Connect remote MCP servers that every workspace inherits. A workspace or personal connection with the same name replaces one of these in its runs. New connections preselect all discovered tools; review the selection and save to enable them.",
      queryKey: ["instanceMCPs"],
      list: api.getInstanceMCPs,
      save: api.saveInstanceMCP,
      remove: api.deleteInstanceMCP,
      revealHeaders: api.revealInstanceMCPHeaders,
      discover: api.discoverInstanceMCP,
    },
    workspace: {
      description:
        "Connect remote MCP servers for this workspace's runs. A connection here replaces an inherited instance connection with the same name. New connections preselect all discovered tools; review the selection and save to enable them.",
      queryKey: ["workspaceMCPs", workspace],
      list: () => api.getWorkspaceMCPs(workspace),
      save: (body) => api.saveWorkspaceMCP(workspace, body),
      remove: (name) => api.deleteWorkspaceMCP(workspace, name),
      revealHeaders: (name) => api.revealWorkspaceMCPHeaders(workspace, name),
      discover: (body) => api.discoverWorkspaceMCP(workspace, body),
    },
    user: {
      description:
        "Connect remote MCP servers with your own credentials. They load only in your private threads, never in threads other people can prompt. A personal connection replaces a workspace connection with the same name in your runs. New connections preselect all discovered tools; review the selection and save to enable them.",
      queryKey: ["myMCPs"],
      list: api.getMyMCPs,
      save: api.saveMyMCP,
      remove: api.deleteMyMCP,
      revealHeaders: api.revealMyMCPHeaders,
      discover: api.discoverMyMCP,
    },
  }
  return scopes[scope]
}

export function MCPConnectionsSection({
  scope,
  workspace = DEFAULT_WORKSPACE_SLUG,
}: {
  scope: MCPScope
  /** Only meaningful for `scope: "workspace"`; ignored for personal MCPs. */
  workspace?: string
}) {
  const { description, queryKey, ...client } = scopeConfig(scope, workspace)
  const qc = useQueryClient()
  const connections = useQuery({ queryKey, queryFn: client.list })
  // What this workspace inherits; shown so an admin can see what a same-named
  // connection here would replace.
  const inherited = useQuery({
    queryKey: ["instanceMCPs"],
    queryFn: api.getInstanceMCPs,
    enabled: scope === "workspace",
  })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [headers, setHeaders] = useState<Header[]>([])
  const [replaceHeaders, setReplaceHeaders] = useState(false)
  const [savedHeaders, setSavedHeaders] = useState<Record<
    string,
    string
  > | null>(null)
  const [catalog, setCatalog] = useState<Catalog>([])
  const [busy, setBusy] = useState(false)
  const [pendingRows, setPendingRows] = useState<ReadonlySet<string>>(
    () => new Set()
  )
  const rowWritesInFlight = useRef(0)
  const [error, setError] = useState<string | null>(null)
  const [toolsExpanded, setToolsExpanded] = useState(true)
  const [importing, setImporting] = useState(false)
  const [pendingImports, setPendingImports] = useState<ImportedMCP[]>([])
  const toolsId = useId()
  const editorId = useId()
  const savedConnection = connections.data?.find(
    (connection) => connection.name === draft?.name
  )
  const dirty =
    draft !== null &&
    (editableValues(draft) !==
      editableValues(draft.existing ? savedConnection : undefined) ||
      (replaceHeaders &&
        (headers.length > 0 ||
          (savedConnection?.header_names.length ?? 0) > 0)))
  const toolDescriptions = new Map(
    catalog.map((tool) => [tool.name, tool.description])
  )
  const selectedTools = new Set(draft?.allowed_tools)
  const toolNames = [
    ...new Set([
      ...toolDescriptions.keys(),
      ...(savedConnection?.allowed_tools ?? []),
      ...selectedTools,
    ]),
  ].sort()

  const openEditor = (
    connection: Draft,
    authentication?: Record<string, string> | null
  ) => {
    setDraft(connection)
    setHeaders(
      Object.entries(authentication ?? {}).map(([name, value]) => ({
        name,
        value,
      }))
    )
    setSavedHeaders(null)
    setReplaceHeaders(authentication != null || !connection.existing)
    setCatalog([])
    setToolsExpanded(true)
    setError(null)
  }

  const edit = (connection?: MCPConnection) => {
    setImporting(false)
    setPendingImports([])
    openEditor(
      connection
        ? { ...connection, existing: true }
        : {
            name: "",
            url: "",
            transport: "streamable_http",
            enabled: true,
            allowed_tools: [],
            existing: false,
          }
    )
  }

  const openImported = ({
    headers: authentication,
    ...connection
  }: ImportedMCP) => {
    const previous = connections.data?.find(
      (saved) => saved.name === connection.name
    )
    openEditor(
      {
        ...connection,
        oauth:
          connection.oauth === undefined ? previous?.oauth : connection.oauth,
        enabled: previous?.enabled ?? true,
        allowed_tools: previous?.allowed_tools ?? [],
        existing: Boolean(previous),
      },
      authentication
    )
  }

  const closeEditor = () => {
    setDraft(null)
    setHeaders([])
    setSavedHeaders(null)
    setPendingImports([])
    setError(null)
  }

  const finishEditing = () => {
    const [next, ...rest] = pendingImports
    if (next) {
      setPendingImports(rest)
      openImported(next)
    } else closeEditor()
  }

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Unable to update MCP connection"
      )
    }
    setBusy(false)
  }

  const optimistic = async (
    name: string,
    errorTitle: string,
    apply: (list: MCPConnection[]) => MCPConnection[],
    revert: (list: MCPConnection[]) => MCPConnection[],
    action: () => Promise<void>
  ) => {
    setPendingRows((rows) => new Set(rows).add(name))
    rowWritesInFlight.current++
    await qc.cancelQueries({ queryKey })
    qc.setQueryData<MCPConnection[]>(
      queryKey,
      (current) => current && apply(current)
    )
    try {
      await action()
    } catch (e) {
      qc.setQueryData<MCPConnection[]>(
        queryKey,
        (current) => current && revert(current)
      )
      reportError({ title: errorTitle, error: e })
    } finally {
      setPendingRows((rows) => {
        const next = new Set(rows)
        next.delete(name)
        return next
      })
      rowWritesInFlight.current--
    }
    // A refetch while another row's write is pending would revert that row.
    if (rowWritesInFlight.current === 0)
      await qc.invalidateQueries({ queryKey })
  }

  const save = async (discover: boolean) => {
    if (!draft) return
    await run(async () => {
      const authentication: Record<string, string> = {}
      if (replaceHeaders) {
        for (const header of headers) {
          const name = header.name.trim()
          if (!name || !header.value.trim())
            throw new Error("Each header needs a name and value")
          if (
            Object.keys(authentication).some(
              (key) => key.toLowerCase() === name.toLowerCase()
            )
          )
            throw new Error("Header names must be unique")
          authentication[name] = header.value
        }
      }
      if (
        !draft.existing &&
        connections.data?.some((c) => c.name === draft.name)
      )
        throw new Error("A connection with this name already exists")
      const update: MCPConnectionUpdate = {
        name: draft.name,
        url: draft.url,
        transport: draft.transport,
        enabled: draft.enabled,
        allowed_tools: draft.allowed_tools,
        headers: replaceHeaders ? authentication : null,
        oauth: draft.oauth ?? null,
      }
      const discovered = discover ? await client.discover(update) : []
      const saved = await client.save(update)
      qc.setQueryData<MCPConnection[]>(queryKey, (current) => [
        ...(current ?? []).filter(
          (connection) => connection.name !== saved.name
        ),
        saved,
      ])
      setDraft({
        ...saved,
        existing: true,
        allowed_tools:
          discover && !draft.existing
            ? discovered.map((tool) => tool.name)
            : saved.allowed_tools,
      })
      setHeaders([])
      setSavedHeaders(null)
      setReplaceHeaders(false)
      await qc.invalidateQueries({ queryKey })
      if (discover) {
        setCatalog(discovered)
        setToolsExpanded(true)
      } else finishEditing()
    })
  }

  const updateHeader = (
    index: number,
    field: "name" | "value",
    value: string
  ) =>
    setHeaders(
      headers.map((header, i) =>
        i === index ? { ...header, [field]: value } : header
      )
    )

  const editor = draft ? (
    <form
      id={editorId}
      className={
        draft.existing
          ? "space-y-4 border-t border-default p-space-4"
          : "space-y-4 rounded-md border border-default p-space-4"
      }
      onSubmit={(event) => {
        event.preventDefault()
        void save(false)
      }}
    >
      <fieldset disabled={busy} className="space-y-4">
        {error && (
          <p role="alert" className="text-sm text-error-secondary">
            {error}
          </p>
        )}
        {pendingImports.length > 0 && (
          <p className="text-xs text-secondary">
            {pendingImports.length} more{" "}
            {pendingImports.length === 1 ? "connection" : "connections"} to
            review after saving.
          </p>
        )}
        <Input
          label="Connection name"
          aria-label="Connection name"
          size="md"
          required
          pattern={"[a-z][a-z0-9_\\-]{0,31}"}
          maxLength={32}
          title="Start with a lowercase letter; use lowercase letters, numbers, hyphens, or underscores."
          placeholder="incident"
          hintText="Use a lowercase name such as incident. Dots and spaces are not allowed."
          disabled={draft.existing}
          value={draft.name}
          onChange={(name) => setDraft({ ...draft, name })}
        />
        <Input
          label="Server URL"
          aria-label="Server URL"
          size="md"
          required
          type="url"
          placeholder="https://example.com/mcp"
          value={draft.url}
          onChange={(url) => setDraft({ ...draft, url })}
        />
        <div className="space-y-1">
          <span className="block text-sm font-medium text-primary">
            Transport
          </span>
          <Select
            aria-label="Transport"
            size="md"
            options={TRANSPORTS}
            value={draft.transport}
            onChange={(transport) =>
              transport && setDraft({ ...draft, transport })
            }
          />
        </div>
        <div className="space-y-1">
          <span className="block text-sm font-medium text-primary">
            Authentication
          </span>
          <Select
            aria-label="Authentication"
            size="md"
            options={AUTH_MODES}
            value={draft.oauth ? "oauth" : "headers"}
            onChange={(mode) =>
              mode &&
              setDraft({
                ...draft,
                oauth:
                  mode === "oauth"
                    ? {
                        grant_type: "client_credentials",
                        token_url: "",
                        client_id: "",
                        scope: "",
                        token_endpoint_auth_method: "client_secret_post",
                      }
                    : null,
              })
            }
          />
        </div>
        {draft.oauth && (
          <MCPOAuthFields
            key={draft.name}
            value={draft.oauth}
            hasSavedSecret={Boolean(
              draft.existing &&
              savedConnection?.oauth &&
              draft.url.trim() === savedConnection.url &&
              draft.oauth.token_url.trim() ===
                savedConnection.oauth.token_url &&
              draft.oauth.client_id === savedConnection.oauth.client_id
            )}
            onChange={(oauth) => setDraft({ ...draft, oauth })}
          />
        )}
        <div className="space-y-2">
          <p className="text-sm font-medium text-primary">
            {draft.oauth ? "Additional headers" : "Authentication headers"}
          </p>
          <p className="text-xs text-secondary">
            {draft.oauth
              ? "Optional headers are encrypted. OAuth supplies the Authorization header automatically."
              : "Values are encrypted and hidden by default. Use a header such as Authorization or X-API-Key."}
          </p>
          {!replaceHeaders ? (
            <>
              {savedHeaders && (
                <div className="space-y-2" data-dd-privacy="hidden">
                  {Object.entries(savedHeaders).map(([name, value]) => (
                    <Input
                      key={name}
                      label={name}
                      aria-label={`Saved ${name} value`}
                      size="md"
                      value={value}
                      onChange={() => {}}
                      readOnly
                      autoComplete="off"
                      spellCheck={false}
                    />
                  ))}
                  {Object.keys(savedHeaders).length === 0 && (
                    <p className="text-xs text-secondary">No saved headers.</p>
                  )}
                </div>
              )}
              <div className="flex flex-wrap gap-space-2">
                <IconButton
                  type="button"
                  size="sm"
                  color="secondary"
                  variant="outlined"
                  icon={savedHeaders ? EyeSlashIcon : EyeIcon}
                  label={
                    savedHeaders ? "Hide saved headers" : "Show saved headers"
                  }
                  aria-expanded={savedHeaders !== null}
                  onClick={() => {
                    if (savedHeaders) setSavedHeaders(null)
                    else
                      void run(async () => {
                        setSavedHeaders(await client.revealHeaders(draft.name))
                      })
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  color="secondary"
                  variant="outlined"
                  onClick={() => {
                    setSavedHeaders(null)
                    setReplaceHeaders(true)
                  }}
                >
                  Replace headers
                </Button>
              </div>
            </>
          ) : (
            <>
              {headers.map((header, index) => (
                <div className="flex gap-space-2" key={index}>
                  <Input
                    aria-label={`Header ${index + 1} name`}
                    size="md"
                    placeholder="Authorization"
                    value={header.name}
                    onChange={(name) => updateHeader(index, "name", name)}
                  />
                  <SecretInput
                    label={`Header ${index + 1} value`}
                    placeholder="Bearer …"
                    revealed={Boolean(header.revealed)}
                    onRevealedChange={(revealed) =>
                      setHeaders(
                        headers.map((item, i) =>
                          i === index ? { ...item, revealed } : item
                        )
                      )
                    }
                    value={header.value}
                    onChange={(event) =>
                      updateHeader(index, "value", event.target.value)
                    }
                  />
                  <Button
                    type="button"
                    size="md"
                    color="secondary"
                    variant="outlined"
                    aria-label={`Remove header ${index + 1}`}
                    onClick={() =>
                      setHeaders(headers.filter((_, i) => i !== index))
                    }
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                size="sm"
                color="secondary"
                variant="outlined"
                onClick={() =>
                  setHeaders([...headers, { name: "", value: "" }])
                }
              >
                Add header
              </Button>
              {draft.existing && headers.length === 0 && (
                <p className="text-xs text-secondary">
                  Saving with no headers clears the saved authentication.
                </p>
              )}
            </>
          )}
        </div>
        <div className="space-y-2">
          <p className="text-sm font-medium text-primary">Allowed tools</p>
          <p className="text-xs text-secondary">
            Discover tools, review the selection, then save. All discovered
            tools are selected by default for new connections.
          </p>
          {toolNames.length > 0 && (
            <>
              <div className="flex flex-wrap items-center gap-space-2">
                <span className="mr-auto text-xs text-secondary">
                  {draft.allowed_tools.length} of {toolNames.length} selected
                </span>
                <Button
                  type="button"
                  size="sm"
                  color="secondary"
                  variant="outlined"
                  disabled={draft.allowed_tools.length === toolNames.length}
                  onClick={() =>
                    setDraft({ ...draft, allowed_tools: toolNames })
                  }
                >
                  Select all
                </Button>
                <Button
                  type="button"
                  size="sm"
                  color="secondary"
                  variant="outlined"
                  disabled={draft.allowed_tools.length === 0}
                  onClick={() => setDraft({ ...draft, allowed_tools: [] })}
                >
                  Clear all
                </Button>
                <Button
                  type="button"
                  size="sm"
                  color="secondary"
                  variant="plain"
                  aria-expanded={toolsExpanded}
                  aria-controls={toolsId}
                  onClick={() => setToolsExpanded(!toolsExpanded)}
                >
                  {toolsExpanded ? "Hide tools" : "Show tools"}
                </Button>
              </div>
              <div
                id={toolsId}
                role="region"
                aria-label="Available tools"
                tabIndex={0}
                hidden={!toolsExpanded}
                className="max-h-80 space-y-3 overflow-y-auto overscroll-contain rounded-md border border-default p-space-3"
              >
                {toolNames.map((name) => (
                  <label
                    key={name}
                    className="flex items-start gap-space-2 text-sm text-primary"
                  >
                    <Checkbox
                      containerClassName="mt-0.5 shrink-0"
                      aria-label={`Allow ${name}`}
                      checked={selectedTools.has(name)}
                      onCheckedChange={(checked) =>
                        setDraft({
                          ...draft,
                          allowed_tools:
                            checked === true
                              ? [...draft.allowed_tools, name]
                              : draft.allowed_tools.filter(
                                  (tool) => tool !== name
                                ),
                        })
                      }
                    />
                    <span className="min-w-0 break-words">
                      {name}
                      <span className="block text-xs text-secondary">
                        {toolDescriptions.get(name)}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-space-2">
          <Button
            type="button"
            size="sm"
            color="secondary"
            variant="outlined"
            disabled={!draft.name || !draft.url}
            onClick={(event) => {
              if (event.currentTarget.form?.reportValidity()) void save(true)
            }}
          >
            Save and discover tools
          </Button>
          <Button type="submit" size="sm" color="primary">
            Save connection
          </Button>
          {pendingImports.length > 0 && (
            <Button
              type="button"
              size="sm"
              color="secondary"
              variant="plain"
              onClick={finishEditing}
            >
              Skip connection
            </Button>
          )}
          <Button
            type="button"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={closeEditor}
          >
            {dirty ? "Cancel" : "Close"}
          </Button>
        </div>
      </fieldset>
    </form>
  ) : null

  return (
    <SettingsSection title="MCP servers" description={description}>
      <div className="space-y-4 p-space-4">
        {connections.isLoading && (
          <p className="text-sm text-secondary">Loading connections…</p>
        )}
        {connections.error && (
          <p role="alert" className="text-sm text-error-secondary">
            {connections.error.message}
          </p>
        )}
        {scope === "workspace" &&
          inherited.data &&
          inherited.data.length > 0 && (
            <section
              aria-label="Inherited instance MCP connections"
              className="rounded-md border border-dashed border-default"
            >
              <p className="px-space-3 pt-space-3 text-xs font-medium text-secondary">
                Inherited from the instance
              </p>
              <ul className="divide-y divide-default">
                {inherited.data.map((connection) => {
                  const replaced = connections.data?.some(
                    (own) => own.name === connection.name
                  )
                  return (
                    <li
                      key={connection.name}
                      className="flex flex-wrap items-center justify-between gap-space-3 p-space-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-primary">
                          {connection.name}{" "}
                          <span className="text-secondary">
                            · {connection.enabled ? "Enabled" : "Disabled"} ·{" "}
                            {connection.allowed_tools.length} tools
                          </span>
                        </p>
                        <p className="text-xs break-all text-secondary">
                          {connection.url}
                        </p>
                      </div>
                      <span className="text-xs text-secondary">
                        {replaced
                          ? "Replaced by this workspace's connection"
                          : "Edit under Admin"}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}
        {connections.data?.map((connection) => {
          const isEditing = draft?.existing && draft.name === connection.name
          return (
            <section
              key={connection.name}
              aria-label={`${connection.name} MCP connection`}
              className="rounded-md border border-default"
            >
              <div className="flex flex-wrap items-center justify-between gap-space-3 p-space-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-primary">
                    {connection.name}{" "}
                    <span className="text-secondary">
                      · {connection.enabled ? "Enabled" : "Disabled"} ·{" "}
                      {connection.allowed_tools.length} tools
                    </span>
                  </p>
                  <p className="text-xs break-all text-secondary">
                    {connection.url}
                  </p>
                </div>
                <div className="flex gap-space-2">
                  <Button
                    size="xs"
                    color="secondary"
                    variant="outlined"
                    disabled={busy}
                    onClick={() => {
                      if (isEditing) closeEditor()
                      else edit(connection)
                    }}
                    aria-expanded={Boolean(isEditing)}
                    aria-controls={isEditing ? editorId : undefined}
                    aria-label={`${isEditing ? "Close" : "Edit"} ${connection.name}`}
                  >
                    {isEditing ? "Close" : "Edit"}
                  </Button>
                  <Button
                    size="xs"
                    color="secondary"
                    variant="outlined"
                    disabled={busy || pendingRows.has(connection.name)}
                    onClick={() => {
                      const withEnabled =
                        (enabled: boolean) => (list: MCPConnection[]) =>
                          list.map((c) =>
                            c.name === connection.name ? { ...c, enabled } : c
                          )
                      void optimistic(
                        connection.name,
                        `Couldn't ${connection.enabled ? "disable" : "enable"} ${connection.name}`,
                        withEnabled(!connection.enabled),
                        withEnabled(connection.enabled),
                        async () => {
                          await client.save({
                            name: connection.name,
                            url: connection.url,
                            transport: connection.transport,
                            enabled: !connection.enabled,
                            allowed_tools: connection.allowed_tools,
                          })
                          if (draft?.name === connection.name) closeEditor()
                        }
                      )
                    }}
                    aria-label={`${connection.enabled ? "Disable" : "Enable"} ${connection.name}`}
                  >
                    {connection.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    size="xs"
                    color="error"
                    variant="outlined"
                    disabled={busy || pendingRows.has(connection.name)}
                    onClick={() =>
                      void optimistic(
                        connection.name,
                        `Couldn't delete ${connection.name}`,
                        (list) =>
                          list.filter((c) => c.name !== connection.name),
                        (list) =>
                          list.some((c) => c.name === connection.name)
                            ? list
                            : [...list, connection],
                        async () => {
                          await client.remove(connection.name)
                          if (draft?.name === connection.name) closeEditor()
                        }
                      )
                    }
                    aria-label={`Delete ${connection.name}`}
                  >
                    Delete
                  </Button>
                </div>
              </div>
              {isEditing && editor}
            </section>
          )
        })}
        {importing ? (
          <MCPImport
            onImport={([first, ...rest]) => {
              if (!first) return
              setImporting(false)
              setPendingImports(rest)
              openImported(first)
            }}
            onCancel={() => setImporting(false)}
          />
        ) : !draft ? (
          <div className="flex gap-space-2">
            <Button
              size="xs"
              color="primary"
              disabled={connections.isPending || connections.isError}
              onClick={() => edit()}
            >
              Add MCP server
            </Button>
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={connections.isPending || connections.isError}
              onClick={() => {
                setImporting(true)
                setError(null)
              }}
            >
              Import JSON
            </Button>
          </div>
        ) : (
          !draft.existing && editor
        )}
      </div>
    </SettingsSection>
  )
}
