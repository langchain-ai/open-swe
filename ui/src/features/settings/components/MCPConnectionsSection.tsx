import { useId, useRef, useState } from "react"
import type { ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  FormField,
  FormSection,
  FormStack,
} from "@langchain/gtm-platform-design-system/patterns/form-field"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@langchain/gtm-platform-design-system/ui/select"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import { AlertTriangle, Eye, EyeOff, Server } from "@/components/glyphs"
import { api, DEFAULT_WORKSPACE_SLUG } from "@/lib/api"
import type { MCPConnection, MCPConnectionUpdate } from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { MCPImport } from "./MCPImport"
import type { ImportedMCP } from "./MCPImport"
import { MCPOAuthFields } from "./MCPOAuthFields"

type Header = { name: string; value: string; revealed?: boolean }
type Draft = Omit<MCPConnectionUpdate, "headers"> & { existing: boolean }
type Catalog = { name: string; description: string }[]

type Transport = MCPConnection["transport"]
type AuthMode = "headers" | "oauth"

const TRANSPORTS: Array<{ value: Transport; label: string }> = [
  { value: "streamable_http", label: "Streamable HTTP" },
  { value: "sse", label: "SSE" },
]

const AUTH_MODES: Array<{ value: AuthMode; label: string }> = [
  { value: "headers", label: "Headers / API key" },
  { value: "oauth", label: "OAuth client credentials" },
]

function isTransport(value: unknown): value is Transport {
  return TRANSPORTS.some((transport) => transport.value === value)
}

/** One block in the connections panel; the panel's hairlines come from these. */
const PANEL_BLOCK_CLASS = "border-b border-line last:border-b-0"

function ConnectionSummary({
  connection,
  emphasis,
}: {
  connection: MCPConnection
  emphasis: boolean
}) {
  return (
    <Stack gap="none" className="min-w-0 flex-1">
      <Box
        render={<p />}
        className={
          emphasis ? "text-label font-medium text-ink" : "text-label text-ink"
        }
      >
        {connection.name}{" "}
        <Box render={<span />} className="font-normal text-ink-subtle">
          · {connection.enabled ? "Enabled" : "Disabled"} ·{" "}
          {connection.allowed_tools.length} tools
        </Box>
      </Box>
      <Box
        render={<p />}
        className="font-mono text-meta break-all text-ink-subtle"
      >
        {connection.url}
      </Box>
    </Stack>
  )
}

function FormFailure({ children }: { children: ReactNode }) {
  return (
    <Inline
      role="alert"
      gap="sm"
      align="start"
      className="text-label text-risk"
    >
      <Icon icon={AlertTriangle} size="sm" />
      <Box render={<span />}>{children}</Box>
    </Inline>
  )
}

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
        "Connect remote MCP servers that every workspace inherits. A workspace or personal connection with the same name replaces one of these in its runs.",
      queryKey: ["instanceMCPs"],
      list: api.getInstanceMCPs,
      save: api.saveInstanceMCP,
      remove: api.deleteInstanceMCP,
      revealHeaders: api.revealInstanceMCPHeaders,
      discover: api.discoverInstanceMCP,
    },
    workspace: {
      description:
        "Connect remote MCP servers for this workspace's runs. A connection here replaces an inherited instance connection with the same name.",
      queryKey: ["workspaceMCPs", workspace],
      list: () => api.getWorkspaceMCPs(workspace),
      save: (body) => api.saveWorkspaceMCP(workspace, body),
      remove: (name) => api.deleteWorkspaceMCP(workspace, name),
      revealHeaders: (name) => api.revealWorkspaceMCPHeaders(workspace, name),
      discover: (body) => api.discoverWorkspaceMCP(workspace, body),
    },
    user: {
      description:
        "Connect remote MCP servers with your own credentials. They load only in your private threads, never in threads other people can prompt. A personal connection replaces a workspace connection with the same name in your runs.",
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
    <Stack
      render={
        <form
          id={editorId}
          onSubmit={(event) => {
            event.preventDefault()
            void save(false)
          }}
        />
      }
      gap="lg"
      className={cn(
        "px-5 py-4",
        draft.existing ? "border-t border-line" : PANEL_BLOCK_CLASS
      )}
    >
      <Box render={<fieldset disabled={busy} />} className="m-0 min-w-0 p-0">
        <FormStack>
          {pendingImports.length > 0 && (
            <Box render={<p />} className="text-meta text-ink-subtle">
              {pendingImports.length} more{" "}
              {pendingImports.length === 1 ? "connection" : "connections"} to
              review after saving.
            </Box>
          )}
          <FormSection title="Connection">
            <FormField
              label="Connection name"
              help="Use a lowercase name such as incident. Dots and spaces are not allowed."
              required
              control={
                <Input
                  required
                  pattern={"[a-z][a-z0-9_\\-]{0,31}"}
                  maxLength={32}
                  title="Start with a lowercase letter; use lowercase letters, numbers, hyphens, or underscores."
                  placeholder="incident"
                  disabled={draft.existing}
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              }
            />
            <FormField
              label="Server URL"
              required
              control={
                <Input
                  required
                  type="url"
                  placeholder="https://example.com/mcp"
                  value={draft.url}
                  onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                />
              }
            />
            <FormField
              label="Transport"
              control={
                <Select
                  items={TRANSPORTS}
                  value={draft.transport}
                  onValueChange={(transport) => {
                    if (isTransport(transport))
                      setDraft({ ...draft, transport })
                  }}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TRANSPORTS.map((transport) => (
                      <SelectItem key={transport.value} value={transport.value}>
                        {transport.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              }
            />
            <FormField
              label="Authentication"
              control={
                <Select
                  items={AUTH_MODES}
                  value={draft.oauth ? "oauth" : "headers"}
                  onValueChange={(mode) =>
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
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {AUTH_MODES.map((mode) => (
                      <SelectItem key={mode.value} value={mode.value}>
                        {mode.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              }
            />
          </FormSection>
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
          <FormSection
            title={
              draft.oauth ? "Additional headers" : "Authentication headers"
            }
            description={
              draft.oauth
                ? "Optional headers are encrypted. OAuth supplies the Authorization header automatically."
                : "Values are encrypted and hidden by default. Use a header such as Authorization or X-API-Key."
            }
          >
            {!replaceHeaders ? (
              <>
                {savedHeaders && (
                  <Stack gap="lg" data-dd-privacy="hidden">
                    {Object.entries(savedHeaders).map(([name, value]) => (
                      <FormField
                        key={name}
                        label={name}
                        control={
                          <Input
                            aria-label={`Saved ${name} value`}
                            value={value}
                            readOnly
                            autoComplete="off"
                            spellCheck={false}
                          />
                        }
                      />
                    ))}
                    {Object.keys(savedHeaders).length === 0 && (
                      <Box render={<p />} className="text-meta text-ink-subtle">
                        No saved headers.
                      </Box>
                    )}
                  </Stack>
                )}
                <Inline gap="sm" wrap>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="outline"
                    aria-label={
                      savedHeaders ? "Hide saved headers" : "Show saved headers"
                    }
                    title={
                      savedHeaders ? "Hide saved headers" : "Show saved headers"
                    }
                    aria-expanded={savedHeaders !== null}
                    onClick={() => {
                      if (savedHeaders) setSavedHeaders(null)
                      else
                        void run(async () => {
                          setSavedHeaders(
                            await client.revealHeaders(draft.name)
                          )
                        })
                    }}
                  >
                    <Icon icon={savedHeaders ? EyeOff : Eye} size="sm" />
                  </Button>
                  <Button
                    type="button"
                    size="compact"
                    variant="outline"
                    onClick={() => {
                      setSavedHeaders(null)
                      setReplaceHeaders(true)
                    }}
                  >
                    Replace headers
                  </Button>
                </Inline>
              </>
            ) : (
              <>
                {headers.map((header, index) => (
                  <Inline gap="sm" align="center" key={index}>
                    <Input
                      aria-label={`Header ${index + 1} name`}
                      placeholder="Authorization"
                      value={header.name}
                      onChange={(e) =>
                        updateHeader(index, "name", e.target.value)
                      }
                    />
                    <Input
                      aria-label={`Header ${index + 1} value`}
                      placeholder="Bearer …"
                      type={header.revealed ? "text" : "password"}
                      autoComplete="off"
                      spellCheck={false}
                      data-dd-privacy="hidden"
                      value={header.value}
                      onChange={(e) =>
                        updateHeader(index, "value", e.target.value)
                      }
                    />
                    <Button
                      type="button"
                      size="icon"
                      variant="outline"
                      aria-label={`${header.revealed ? "Hide" : "Show"} header ${index + 1} value`}
                      title={header.revealed ? "Hide value" : "Show value"}
                      aria-pressed={Boolean(header.revealed)}
                      onClick={() =>
                        setHeaders(
                          headers.map((item, i) =>
                            i === index
                              ? { ...item, revealed: !item.revealed }
                              : item
                          )
                        )
                      }
                    >
                      <Icon icon={header.revealed ? EyeOff : Eye} size="md" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      aria-label={`Remove header ${index + 1}`}
                      onClick={() =>
                        setHeaders(headers.filter((_, i) => i !== index))
                      }
                    >
                      Remove
                    </Button>
                  </Inline>
                ))}
                <Inline gap="md" align="center" wrap>
                  <Button
                    type="button"
                    size="compact"
                    variant="outline"
                    onClick={() =>
                      setHeaders([...headers, { name: "", value: "" }])
                    }
                  >
                    Add header
                  </Button>
                  {draft.existing && headers.length === 0 && (
                    <Box render={<p />} className="text-meta text-ink-subtle">
                      Saving with no headers clears the saved authentication.
                    </Box>
                  )}
                </Inline>
              </>
            )}
          </FormSection>
          <FormSection
            title="Allowed tools"
            description="Discover tools, review the selection, then save. All discovered tools are selected by default for new connections."
          >
            {toolNames.length > 0 && (
              <Stack gap="sm">
                <Inline gap="sm" align="center" wrap>
                  <Box
                    render={<span />}
                    className="mr-auto text-meta text-ink-subtle tabular-nums"
                  >
                    {draft.allowed_tools.length} of {toolNames.length} selected
                  </Box>
                  <Button
                    type="button"
                    size="compact"
                    variant="outline"
                    disabled={draft.allowed_tools.length === toolNames.length}
                    onClick={() =>
                      setDraft({ ...draft, allowed_tools: toolNames })
                    }
                  >
                    Select all
                  </Button>
                  <Button
                    type="button"
                    size="compact"
                    variant="outline"
                    disabled={draft.allowed_tools.length === 0}
                    onClick={() => setDraft({ ...draft, allowed_tools: [] })}
                  >
                    Clear all
                  </Button>
                  <Button
                    type="button"
                    size="compact"
                    variant="ghost"
                    aria-expanded={toolsExpanded}
                    aria-controls={toolsId}
                    onClick={() => setToolsExpanded(!toolsExpanded)}
                  >
                    {toolsExpanded ? "Hide tools" : "Show tools"}
                  </Button>
                </Inline>
                <Box
                  id={toolsId}
                  role="region"
                  aria-label="Available tools"
                  hidden={!toolsExpanded}
                  border="line"
                  radius="compact"
                  className="overflow-hidden"
                >
                  <ScrollArea overflow="vertical" viewportClassName="max-h-80">
                    <Stack gap="md" padding="md">
                      {toolNames.map((name) => (
                        <Inline
                          key={name}
                          gap="sm"
                          align="start"
                          className="text-label text-ink"
                        >
                          <Checkbox
                            className="mt-0.5"
                            aria-label={`Allow ${name}`}
                            checked={selectedTools.has(name)}
                            onCheckedChange={(checked) =>
                              setDraft({
                                ...draft,
                                allowed_tools: checked
                                  ? [...draft.allowed_tools, name]
                                  : draft.allowed_tools.filter(
                                      (tool) => tool !== name
                                    ),
                              })
                            }
                          />
                          <Stack gap="none" className="min-w-0 break-words">
                            <Box render={<span />} className="font-mono">
                              {name}
                            </Box>
                            <Box
                              render={<span />}
                              className="text-meta text-ink-subtle"
                            >
                              {toolDescriptions.get(name)}
                            </Box>
                          </Stack>
                        </Inline>
                      ))}
                    </Stack>
                  </ScrollArea>
                </Box>
              </Stack>
            )}
          </FormSection>
          <Stack gap="md">
            {error && <FormFailure>{error}</FormFailure>}
            <Inline gap="sm" align="center" wrap>
              <Button type="submit" size="compact">
                Save connection
              </Button>
              <Button
                type="button"
                size="compact"
                variant="outline"
                disabled={!draft.name || !draft.url}
                onClick={(event) => {
                  if (event.currentTarget.form?.reportValidity())
                    void save(true)
                }}
              >
                Save and discover tools
              </Button>
              {pendingImports.length > 0 && (
                <Button
                  type="button"
                  size="compact"
                  variant="ghost"
                  onClick={finishEditing}
                >
                  Skip connection
                </Button>
              )}
              <Button
                type="button"
                size="compact"
                variant="ghost"
                onClick={closeEditor}
              >
                {dirty ? "Cancel" : "Close"}
              </Button>
            </Inline>
          </Stack>
        </FormStack>
      </Box>
    </Stack>
  ) : null

  const ownConnections = connections.data ?? []
  const inheritedConnections =
    scope === "workspace" ? (inherited.data ?? []) : []
  const showPanel =
    connections.isLoading ||
    ownConnections.length > 0 ||
    inheritedConnections.length > 0 ||
    importing ||
    (draft !== null && !draft.existing) ||
    !connections.isError

  return (
    <PageSection
      title="MCP servers"
      description={description}
      actions={
        importing || draft ? undefined : (
          <>
            <Button
              size="compact"
              variant="outline"
              disabled={connections.isPending || connections.isError}
              onClick={() => {
                setImporting(true)
                setError(null)
              }}
            >
              Import JSON
            </Button>
            <Button
              size="compact"
              disabled={connections.isPending || connections.isError}
              onClick={() => edit()}
            >
              Add MCP server
            </Button>
          </>
        )
      }
    >
      {connections.error && (
        <StateNotice
          tone="RISK"
          icon={AlertTriangle}
          title="MCP servers did not load"
          description={`${connections.error.message} Adding a server is off until the list loads, so nothing is overwritten.`}
        />
      )}
      {showPanel && (
        <Stack
          gap="none"
          bg="panel"
          border="line"
          radius="panel"
          className="overflow-hidden"
        >
          {connections.isLoading && (
            <Box padding="lg" className={PANEL_BLOCK_CLASS}>
              <Skeleton className="h-10 w-full" />
            </Box>
          )}
          {ownConnections.map((connection) => {
            const isEditing = draft?.existing && draft.name === connection.name
            return (
              <section
                key={connection.name}
                aria-label={`${connection.name} MCP connection`}
                className={PANEL_BLOCK_CLASS}
              >
                <Inline
                  gap="md"
                  align="center"
                  justify="between"
                  wrap
                  className="px-5 py-3"
                >
                  <ConnectionSummary connection={connection} emphasis />
                  <Inline gap="sm" align="center">
                    <Button
                      size="compact"
                      variant="outline"
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
                      size="compact"
                      variant="outline"
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
                    <ConfirmableAction
                      trigger={
                        <Button
                          size="compact"
                          variant="outline"
                          disabled={busy || pendingRows.has(connection.name)}
                          aria-label={`Delete ${connection.name}`}
                        >
                          Delete
                        </Button>
                      }
                      title={`Delete ${connection.name}?`}
                      description="Runs stop loading this server's tools, and its saved headers and OAuth secret are deleted. This cannot be undone."
                      confirmLabel="Delete connection"
                      // The row leaves at once; a failed delete puts it back and reports.
                      onConfirm={async () => {
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
                      }}
                    />
                  </Inline>
                </Inline>
                {isEditing && editor}
              </section>
            )
          })}
          {importing ? (
            <Box className={cn("px-5 py-4", PANEL_BLOCK_CLASS)}>
              <MCPImport
                onImport={([first, ...rest]) => {
                  if (!first) return
                  setImporting(false)
                  setPendingImports(rest)
                  openImported(first)
                }}
                onCancel={() => setImporting(false)}
              />
            </Box>
          ) : (
            draft && !draft.existing && editor
          )}
          {inheritedConnections.length > 0 && (
            <section
              aria-label="Inherited instance MCP connections"
              className={PANEL_BLOCK_CLASS}
            >
              <Box
                render={<p />}
                className="border-b border-line bg-muted px-5 py-2 text-meta font-medium text-ink-subtle"
              >
                Inherited from the instance
              </Box>
              {inheritedConnections.map((connection) => {
                const replaced = ownConnections.some(
                  (own) => own.name === connection.name
                )
                return (
                  <Inline
                    key={connection.name}
                    gap="md"
                    align="center"
                    justify="between"
                    wrap
                    className={cn("px-5 py-3", PANEL_BLOCK_CLASS)}
                  >
                    <ConnectionSummary
                      connection={connection}
                      emphasis={false}
                    />
                    <Badge tier="plain" tone="neutral">
                      {replaced
                        ? "Replaced by this workspace's connection"
                        : "Edit under Admin"}
                    </Badge>
                  </Inline>
                )
              })}
            </section>
          )}
          {!connections.isLoading &&
            !connections.isError &&
            ownConnections.length === 0 &&
            inheritedConnections.length === 0 &&
            !importing &&
            !draft && (
              <EmptyState
                icon={Server}
                title="No MCP servers yet"
                description="Add a server, or import a JSON configuration."
              />
            )}
        </Stack>
      )}
    </PageSection>
  )
}
