import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState, useSyncExternalStore } from "react"
import {
  Cloud,
  Monitor,
  Plug,
  Plus,
  Search,
  Settings2,
  Trash2,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { api, connectService } from "@/lib/api"
import { mcpDesktopBridge } from "@/lib/mcp"
import type { LocalMcpServer, McpConnection, McpScope } from "@/lib/mcp"
import { McpConnectionForm } from "./McpConnectionForm"
import type { McpEditor, McpSave } from "./McpConnectionForm"
import { McpJsonImport } from "./McpJsonImport"
import type { ImportedMCP } from "./McpJsonImport"

const subscribe = () => () => {}
const serverSnapshot = () => false
const desktopSnapshot = () => !!mcpDesktopBridge()
type ServerRow =
  | { source: "cloud"; record: McpConnection }
  | { source: "local"; record: LocalMcpServer }

function requireBridge() {
  const bridge = mcpDesktopBridge()
  if (!bridge)
    throw new Error("Local MCP support is unavailable in this desktop version.")
  return bridge
}

export function McpConnectionsSection({
  login,
  scope = "user",
  notice,
  onDismissNotice,
}: {
  login: string
  scope?: McpScope
  notice?: { connected?: string; error?: string }
  onDismissNotice?: () => void
}) {
  const qc = useQueryClient()
  const workspace = scope === "workspace"
  const desktop = useSyncExternalStore(
    subscribe,
    desktopSnapshot,
    serverSnapshot
  )
  const localAvailable = desktop && !workspace
  const cloudKey = ["mcpConnections", scope, login]
  const localKey = ["localMcpServers", login]
  const cloud = useQuery({
    queryKey: cloudKey,
    queryFn: () => api.mcpConnections(scope),
  })
  const local = useQuery({
    queryKey: localKey,
    queryFn: () => requireBridge().getMcpServers(),
    enabled: localAvailable,
  })
  const [search, setSearch] = useState("")
  const [editor, setEditor] = useState<McpEditor | null>(null)
  const [importing, setImporting] = useState(false)
  const [pendingImports, setPendingImports] = useState<Array<ImportedMCP>>([])
  const [error, setError] = useState<string | null>(null)
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: cloudKey }),
      qc.invalidateQueries({ queryKey: localKey }),
    ])
  }
  const save = useMutation({
    mutationFn: async (value: McpSave) => {
      if (value.source === "cloud")
        return api.saveMcpConnection(value.record, scope)
      const bridge = requireBridge()
      if (
        !editor?.record &&
        local.data?.some((server) => server.name === value.record.name)
      )
        throw new Error(
          "A server with this name already exists on this device. Edit it instead."
        )
      return bridge.saveMcpServer(value.record)
    },
    onSuccess: refresh,
  })
  const action = useMutation({
    mutationFn: async ({
      row,
      kind,
    }: {
      row: ServerRow
      kind: "toggle" | "delete" | "test" | "authorize"
    }) => {
      setError(null)
      if (row.source === "cloud") {
        if (kind === "delete")
          return api.deleteMcpConnection(row.record.id, scope)
        if (kind === "test") return api.testMcpConnection(row.record.id, scope)
        if (kind === "authorize") {
          // The desktop app runs consent itself; the web redirect never resolves here.
          const pending = connectService(`mcp-connections/${row.record.id}`)
          if (!pending) return new Promise<never>(() => {})
          if (!(await pending))
            throw new Error("Authorization was cancelled or failed.")
          return api.testMcpConnection(row.record.id, scope)
        }
        return api.saveMcpConnection(
          { id: row.record.id, enabled: !row.record.enabled },
          scope
        )
      }
      const bridge = requireBridge()
      if (kind === "delete") return bridge.deleteMcpServer(row.record.name)
      return bridge.saveMcpServer({
        ...row.record,
        enabled: !row.record.enabled,
      })
    },
    onSuccess: refresh,
    onError: (cause: Error) => setError(cause.message),
  })
  const running = (kind: string, id: string) =>
    action.isPending &&
    action.variables?.kind === kind &&
    action.variables.row.source === "cloud" &&
    action.variables.row.record.id === id
  const rows: Array<ServerRow> = [
    ...(localAvailable ? (local.data ?? []) : []).map((record): ServerRow => ({
      source: "local",
      record,
    })),
    ...(cloud.data?.connections ?? []).map((record): ServerRow => ({
      source: "cloud",
      record,
    })),
  ]
  const needle = search.trim().toLowerCase()
  const filtered = rows.filter(({ record, source }) =>
    `${record.name} ${record.url ?? ""} ${source === "local" ? "this device" : "cloud"}`
      .toLowerCase()
      .includes(needle)
  )
  const busy = save.isPending || action.isPending
  const localNames = new Set((local.data ?? []).map((record) => record.name))
  const cloudLabel = workspace ? "Workspace" : "Cloud"

  const openImported = (imported: ImportedMCP) =>
    setEditor({
      source: "cloud",
      record: cloud.data?.connections.find(
        (connection) => connection.name === imported.name
      ),
      imported,
    })
  const nextImport = () => {
    const [next, ...rest] = pendingImports
    setPendingImports(rest)
    if (next) openImported(next)
    else setEditor(null)
  }
  const closeEditor = () => {
    setPendingImports([])
    setEditor(null)
  }

  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        {workspace ? (
          <div />
        ) : (
          <div>
            <h2 className="text-sm font-medium">MCP servers</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Give Open SWE access to your tools, services, and data.
            </p>
          </div>
        )}
        <div className="flex gap-2">
          {workspace && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setImporting(true)
                setError(null)
              }}
              disabled={busy || cloud.isPending || cloud.isError}
            >
              Import JSON
            </Button>
          )}
          <Button
            size="sm"
            onClick={() => setEditor({ source: "cloud" })}
            disabled={busy || (workspace && (cloud.isPending || cloud.isError))}
          >
            <Plus className="size-3.5" />
            Add server
          </Button>
        </div>
      </div>
      <div className="relative">
        <Search className="absolute top-2 left-3 size-3.5 text-muted-foreground" />
        <Input
          aria-label="Search MCP servers"
          className="pl-9"
          placeholder="Search servers…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {!workspace && (notice?.connected || notice?.error) && (
        <div
          role="status"
          className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs ${notice.error ? "border-destructive/40 text-destructive" : "border-border text-muted-foreground"}`}
        >
          <span>
            {notice.error
              ? `Authorization failed: ${notice.error}`
              : `Authorized ${cloud.data?.connections.find((connection) => connection.id === notice.connected)?.name ?? "the MCP server"}.`}
          </span>
          {onDismissNotice && (
            <Button size="sm" variant="ghost" onClick={onDismissNotice}>
              Dismiss
            </Button>
          )}
        </div>
      )}
      {cloud.isError && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 text-xs text-destructive"
        >
          Could not load {workspace ? "workspace" : "cloud"} servers:{" "}
          {cloud.error.message}
          <Button
            size="sm"
            variant="outline"
            onClick={() => void cloud.refetch()}
          >
            Retry
          </Button>
        </div>
      )}
      {local.isError && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 text-xs text-destructive"
        >
          Could not load device servers: {local.error.message}
          <Button
            size="sm"
            variant="outline"
            onClick={() => void local.refetch()}
          >
            Retry
          </Button>
        </div>
      )}
      {cloud.isLoading || (localAvailable && local.isLoading) ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {filtered.map((row) => {
            const { record, source } = row
            const shadowed = source === "cloud" && localNames.has(record.name)
            const status = source === "cloud" ? row.record.status : "local"
            return (
              <div
                key={`${source}:${source === "cloud" ? row.record.id : record.name}`}
                className="flex flex-wrap items-center gap-3 px-4 py-4"
              >
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/40">
                  <Plug className="size-4 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1 basis-40">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-sm font-medium">
                      {record.name}
                    </h3>
                    <span className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      {source === "cloud" ? (
                        <Cloud className="size-3" />
                      ) : (
                        <Monitor className="size-3" />
                      )}
                      {source === "cloud" ? cloudLabel : "This device"}
                    </span>
                    {shadowed && (
                      <span className="text-[10px] text-muted-foreground">
                        Overridden on this device
                      </span>
                    )}
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {source === "local" && row.record.transport === "stdio"
                      ? `stdio · ${row.record.command}`
                      : record.url}
                  </p>
                  <p
                    className={`mt-1 text-[10px] ${status === "error" || status === "auth_required" ? "text-destructive" : "text-muted-foreground"}`}
                  >
                    {!record.enabled
                      ? "Disabled"
                      : status === "connected" && source === "cloud"
                        ? `Connected · ${row.record.allowed_tools?.length ?? row.record.tool_names.length} tools`
                        : status === "auth_required"
                          ? "Authorization required"
                          : status === "error"
                            ? "Connection failed · check settings and test again"
                            : status === "local"
                              ? source === "local" &&
                                row.record.auth_type === "oauth"
                                ? "OAuth · browser sign-in when a local run starts"
                                : "Connects when a local run starts"
                              : "Not tested"}
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {source === "cloud" && (
                    <>
                      {!workspace && row.record.auth_type === "oauth" && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy || !record.enabled}
                          onClick={() =>
                            action.mutate({ row, kind: "authorize" })
                          }
                        >
                          {running("authorize", row.record.id)
                            ? "Authorizing…"
                            : row.record.oauth_configured
                              ? "Reauthorize"
                              : "Authorize"}
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy || !record.enabled}
                        onClick={() => action.mutate({ row, kind: "test" })}
                      >
                        {running("test", row.record.id) ? "Testing…" : "Test"}
                      </Button>
                    </>
                  )}
                  <Switch
                    aria-label={`${record.enabled ? "Disable" : "Enable"} ${record.name} ${source === "cloud" ? (workspace ? "for the workspace" : "globally") : "on this device"}`}
                    title={
                      source === "cloud"
                        ? workspace
                          ? "Applies to every authorized run"
                          : "Applies everywhere for your account"
                        : "Applies to this device"
                    }
                    checked={record.enabled}
                    disabled={busy}
                    onCheckedChange={() =>
                      action.mutate({ row, kind: "toggle" })
                    }
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Edit ${record.name} ${source}`}
                    disabled={busy}
                    onClick={() => setEditor(row)}
                  >
                    <Settings2 className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Delete ${record.name} ${source}`}
                    disabled={busy}
                    onClick={() => {
                      if (
                        window.confirm(
                          `Delete ${record.name} from ${source === "cloud" ? cloudLabel : "this device"}? This cannot be undone.${source === "local" && cloud.data?.connections.some((connection) => connection.name === record.name) ? " The cloud server with this name will apply on this device again." : ""}`
                        )
                      )
                        action.mutate({ row, kind: "delete" })
                    }}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            )
          })}
          {!filtered.length && (
            <div className="px-6 py-10 text-center">
              <Plug className="mx-auto mb-3 size-6 text-muted-foreground" />
              <p className="text-sm font-medium">
                {needle
                  ? "No matching servers"
                  : "Connect your first MCP server"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {needle
                  ? "Try a different name or source."
                  : workspace
                    ? "Add a remote server or import a Claude-style JSON configuration."
                    : "Add a custom server or start with a preset below."}
              </p>
            </div>
          )}
        </div>
      )}
      {importing && (
        <McpJsonImport
          onImport={([first, ...rest]) => {
            if (!first) return
            setImporting(false)
            setPendingImports(rest)
            openImported(first)
          }}
          onCancel={() => setImporting(false)}
        />
      )}
      {!workspace && (
        <p className="text-xs text-muted-foreground">
          Cloud toggles apply to all your runs. This device settings only affect
          local runs; matching local names override cloud servers.
        </p>
      )}
      {!workspace && !localAvailable && (
        <p className="text-xs text-muted-foreground">
          Device servers and stdio require a desktop version with local MCP
          support.
        </p>
      )}
      {!workspace && !!cloud.data?.presets.length && (
        <div className="space-y-3 pt-3">
          <h3 className="text-xs font-medium text-muted-foreground">
            Quick start
          </h3>
          <div className="grid gap-3 sm:grid-cols-3">
            {cloud.data.presets.map((preset) => (
              <button
                key={`${preset.name}:${preset.url}`}
                type="button"
                disabled={busy}
                onClick={() => setEditor({ source: "cloud", preset })}
                className="flex items-center gap-3 rounded-xl border border-border bg-card p-4 text-left transition-colors hover:bg-muted/40 disabled:opacity-50"
              >
                <Plug className="size-4 text-muted-foreground" />
                <span className="flex-1">
                  <span className="block text-xs font-medium">
                    {preset.name}
                  </span>
                  <span className="mt-1 block text-[10px] text-muted-foreground">
                    Connect with OAuth
                  </span>
                </span>
                <Plus className="size-3.5 text-muted-foreground" />
              </button>
            ))}
          </div>
        </div>
      )}
      {editor && (
        <McpConnectionForm
          key={`${editor.source}:${editor.record ? ("id" in editor.record ? editor.record.id : editor.record.name) : ""}:${editor.source === "cloud" ? (editor.imported?.name ?? "") : ""}`}
          editor={editor}
          scope={scope}
          localAvailable={localAvailable}
          pending={save.isPending}
          queued={pendingImports.length}
          onClose={closeEditor}
          onSkip={
            editor.source === "cloud" && editor.imported
              ? nextImport
              : undefined
          }
          onDiscover={async (record) =>
            (await api.discoverMcpConnection(record, scope)).tools
          }
          onRevealHeaders={
            workspace ? api.revealMcpConnectionHeaders : undefined
          }
          onSave={async (value) => {
            await save.mutateAsync(value)
            nextImport()
          }}
        />
      )}
    </section>
  )
}
