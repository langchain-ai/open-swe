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
import { api } from "@/lib/api"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import { mcpDesktopBridge } from "@/lib/mcp"
import type { LocalMcpServer, McpConnection } from "@/lib/mcp"
import { McpConnectionForm } from "./McpConnectionForm"
import type { McpEditor, McpSave } from "./McpConnectionForm"

const subscribe = () => () => {}
const serverSnapshot = () => false
const desktopSnapshot = () => !!mcpDesktopBridge()
type ServerRow =
  | { source: "cloud"; record: McpConnection }
  | { source: "local"; record: LocalMcpServer }

export function McpConnectionsSection({ login }: { login: string }) {
  const qc = useQueryClient()
  const localAvailable = useSyncExternalStore(
    subscribe,
    desktopSnapshot,
    serverSnapshot
  )
  const cloudKey = ["mcpConnections", login]
  const localKey = ["localMcpServers", login]
  const cloud = useQuery({ queryKey: cloudKey, queryFn: api.mcpConnections })
  const local = useQuery({
    queryKey: localKey,
    queryFn: () => {
      const bridge = mcpDesktopBridge()
      if (!bridge)
        throw new Error(
          "Local MCP support is unavailable in this desktop version."
        )
      return bridge.getMcpServers()
    },
    enabled: localAvailable,
  })
  const [search, setSearch] = useState("")
  const [editor, setEditor] = useState<McpEditor | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: cloudKey }),
      qc.invalidateQueries({ queryKey: localKey }),
    ])
  }
  const save = useMutation({
    mutationFn: async (value: McpSave) => {
      if (value.source === "cloud") return api.saveMcpConnection(value.record)
      const bridge = mcpDesktopBridge()
      if (!bridge)
        throw new Error(
          "Local MCP support is unavailable in this desktop version."
        )
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
      kind: "toggle" | "delete" | "test"
    }) => {
      setError(null)
      if (row.source === "cloud") {
        if (kind === "delete") return api.deleteMcpConnection(row.record.id)
        if (kind === "test") return api.testMcpConnection(row.record.id)
        return api.saveMcpConnection({
          id: row.record.id,
          enabled: !row.record.enabled,
        })
      }
      const bridge = mcpDesktopBridge()
      if (!bridge)
        throw new Error(
          "Local MCP support is unavailable in this desktop version."
        )
      if (kind === "delete") return bridge.deleteMcpServer(row.record.name)
      return bridge.saveMcpServer({
        ...row.record,
        enabled: !row.record.enabled,
      })
    },
    onSuccess: refresh,
    onError: (cause: Error) => setError(cause.message),
  })
  const rows: Array<ServerRow> = [
    ...(local.data ?? []).map((record): ServerRow => ({
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

  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-medium">MCP servers</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Give Open SWE access to your tools, services, and data.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setEditor({ source: "cloud" })}
          disabled={busy}
        >
          <Plus className="size-3.5" />
          Add server
        </Button>
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
      {cloud.isError && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 text-xs text-destructive"
        >
          Could not load cloud servers: {cloud.error.message}
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
                      {source === "cloud" ? "Cloud" : "This device"}
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
                        ? `Connected · ${row.record.tool_names.length} tools`
                        : status === "auth_required"
                          ? "Authorization required"
                          : status === "error"
                            ? "Connection failed · check settings and test again"
                            : status === "local"
                              ? "Connects when a local run starts"
                              : "Not tested"}
                  </p>
                </div>
                <div className="flex items-center gap-1.5">
                  {source === "cloud" && (
                    <>
                      {row.record.auth_type === "oauth" && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy || !record.enabled}
                          onClick={() =>
                            window.location.assign(
                              dashboardApiUrl(
                                `/mcp-connections/${encodeURIComponent(row.record.id)}/oauth/login`
                              )
                            )
                          }
                        >
                          {row.record.oauth_configured
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
                        {action.isPending &&
                        action.variables?.kind === "test" &&
                        action.variables.row.source === "cloud" &&
                        action.variables.row.record.id === row.record.id
                          ? "Testing…"
                          : "Test"}
                      </Button>
                    </>
                  )}
                  <Switch
                    aria-label={`${record.enabled ? "Disable" : "Enable"} ${record.name} ${source === "cloud" ? "globally" : "on this device"}`}
                    title={
                      source === "cloud"
                        ? "Applies everywhere for your account"
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
                          `Delete ${record.name} from ${source === "cloud" ? "Cloud" : "this device"}? This cannot be undone.${source === "local" && cloud.data?.connections.some((connection) => connection.name === record.name) ? " The cloud server with this name will apply on this device again." : ""}`
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
                  : "Add a custom server or start with a preset below."}
              </p>
            </div>
          )}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Cloud toggles apply to all your runs. This device settings only affect
        local runs; matching local names override cloud servers.
      </p>
      {!localAvailable && (
        <p className="text-xs text-muted-foreground">
          Device servers and stdio require a desktop version with local MCP
          support.
        </p>
      )}
      {!!cloud.data?.presets.length && (
        <div className="space-y-3 pt-3">
          <h3 className="text-xs font-medium text-muted-foreground">
            Quick start
          </h3>
          <div className="grid gap-3 sm:grid-cols-3">
            {cloud.data.presets.map((preset) => (
              <button
                key={preset.url}
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
          editor={editor}
          localAvailable={localAvailable}
          pending={save.isPending}
          onClose={() => setEditor(null)}
          onSave={async (value) => {
            await save.mutateAsync(value)
            setEditor(null)
          }}
        />
      )}
    </section>
  )
}
