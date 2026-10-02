import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"
import type {
  ManagedSelection,
  ManagedServer,
  ManagedToolsView,
} from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { ConnectLangSmithButton } from "./ConnectionsSection"

const QUERY_KEY = ["myManagedMCPs"]

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** LangSmith Managed Tools servers for the caller; hidden when the instance has none. */
export function useManagedMCPs(enabled = true) {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.getMyManagedMCPs,
    enabled,
  })
}

/** Opens LMT's consent page in a popup and refreshes the catalog when the person returns. */
function useConnectPopup() {
  const qc = useQueryClient()
  const pending = useRef(false)
  const [connecting, setConnecting] = useState<string | null>(null)

  useEffect(() => {
    const refresh = () => {
      if (!pending.current) return
      pending.current = false
      setConnecting(null)
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    }
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [qc])

  const connect = useMutation({
    meta: { errorTitle: "Couldn't open the connection page" },
    mutationFn: async ({
      server,
      popup,
    }: {
      server: ManagedServer
      popup: Window
    }) => {
      const result = await api.connectMyManagedMCP(server.id)
      if (result.url) {
        pending.current = true
        popup.location.replace(result.url)
      } else {
        popup.close()
      }
      return result
    },
    onMutate: ({ server }) => setConnecting(server.id),
    onError: (_e, { popup }) => {
      popup.close()
      setConnecting(null)
    },
    onSuccess: (result) => {
      if (!result.connected) return
      setConnecting(null)
      void qc.invalidateQueries({ queryKey: QUERY_KEY })
    },
  })

  return {
    connecting,
    start: (server: ManagedServer) => {
      // Open synchronously in the click so the browser does not block the popup.
      const popup = window.open("about:blank", "_blank")
      if (!popup) return
      popup.opener = null
      connect.mutate({ server, popup })
    },
  }
}

function useEnable(server: ManagedServer) {
  const qc = useQueryClient()
  return useMutation({
    meta: { errorTitle: `Couldn't add ${server.name}` },
    mutationFn: async () => {
      const tools = await api.discoverMyManagedMCP(server.id)
      return api.saveMyManagedMCP(server.id, {
        enabled: true,
        allowed_tools: tools.map((tool) => tool.name),
      })
    },
    onSettled: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}

function ManagedBadge() {
  return <Badge variant="outline">LangSmith Managed Tool</Badge>
}

function SelectionRow({
  selection,
  server,
  onConnect,
  connecting,
}: {
  selection: ManagedSelection
  server?: ManagedServer
  onConnect: (server: ManagedServer) => void
  connecting: boolean
}) {
  const qc = useQueryClient()
  const withSelections =
    (update: (list: ManagedSelection[]) => ManagedSelection[]) =>
    (view: ManagedToolsView) => ({
      ...view,
      selections: update(view.selections),
    })

  const toggle = useMutation({
    meta: {
      errorTitle: `Couldn't ${selection.enabled ? "disable" : "enable"} ${selection.name}`,
    },
    mutationFn: () =>
      api.saveMyManagedMCP(selection.server_id, {
        enabled: !selection.enabled,
        allowed_tools: selection.allowed_tools,
      }),
    onMutate: async () => ({
      undo: await optimisticUpdate<ManagedToolsView>(
        qc,
        QUERY_KEY,
        withSelections((list) =>
          list.map((item) =>
            item.server_id === selection.server_id
              ? { ...item, enabled: !selection.enabled }
              : item
          )
        )
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  const remove = useMutation({
    meta: { errorTitle: `Couldn't remove ${selection.name}` },
    mutationFn: () => api.deleteMyManagedMCP(selection.server_id),
    onMutate: async () => ({
      undo: await optimisticUpdate<ManagedToolsView>(
        qc,
        QUERY_KEY,
        withSelections((list) =>
          list.filter((item) => item.server_id !== selection.server_id)
        )
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  const needsConnection =
    server !== undefined && server.kind === "oauth" && !server.connected
  const busy = toggle.isPending || remove.isPending

  return (
    <section
      aria-label={`${selection.name} managed MCP connection`}
      className="rounded-md border"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
            {selection.name}
            <ManagedBadge />
            <span className="font-normal text-muted-foreground">
              {selection.enabled ? "Enabled" : "Disabled"} ·{" "}
              {selection.allowed_tools.length} tools
              {needsConnection ? " · Not connected" : ""}
            </span>
          </p>
          <p className="text-xs break-all text-muted-foreground">
            {host(selection.upstream_url)}
          </p>
        </div>
        <div className="flex gap-2">
          {needsConnection && server ? (
            <Button
              size="sm"
              onClick={() => onConnect(server)}
              disabled={connecting}
            >
              {connecting ? "Waiting…" : "Reconnect"}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => toggle.mutate()}
            aria-label={`${selection.enabled ? "Disable" : "Enable"} ${selection.name}`}
          >
            {selection.enabled ? "Disable" : "Enable"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => remove.mutate()}
            aria-label={`Remove ${selection.name}`}
          >
            Remove
          </Button>
        </div>
      </div>
    </section>
  )
}

/** The managed servers this person added, listed with their own personal MCPs. */
export function ManagedMCPRows() {
  const view = useManagedMCPs()
  const popup = useConnectPopup()
  const servers = useMemo(
    () => new Map((view.data?.servers ?? []).map((item) => [item.id, item])),
    [view.data]
  )
  if (!view.data?.configured) return null
  return (
    <>
      {view.data.selections.map((selection) => (
        <SelectionRow
          key={selection.server_id}
          selection={selection}
          server={servers.get(selection.server_id)}
          onConnect={popup.start}
          connecting={popup.connecting === selection.server_id}
        />
      ))}
    </>
  )
}

function PickerRow({
  server,
  onConnect,
  connecting,
  onAdded,
}: {
  server: ManagedServer
  onConnect: (server: ManagedServer) => void
  connecting: boolean
  onAdded: () => void
}) {
  const enable = useEnable(server)
  const ready = server.connected || server.kind === "none"
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-sm font-medium">{server.name}</p>
        <p className="truncate text-xs text-muted-foreground">
          {host(server.upstream_url)}
        </p>
      </div>
      {ready ? (
        <Button
          size="sm"
          disabled={enable.isPending}
          onClick={() => enable.mutate(undefined, { onSuccess: onAdded })}
        >
          {enable.isPending ? "Adding…" : "Add"}
        </Button>
      ) : server.kind === "secret" ? (
        <span className="text-xs text-muted-foreground">
          Set its API key in LangSmith
        </span>
      ) : (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onConnect(server)}
          disabled={connecting}
        >
          {connecting ? "Waiting…" : "Connect"}
        </Button>
      )}
    </li>
  )
}

/** Search the LangSmith Managed Tools catalog and add a server as a personal MCP. */
export function ManagedMCPPicker({ onClose }: { onClose: () => void }) {
  const view = useManagedMCPs()
  const popup = useConnectPopup()
  const [search, setSearch] = useState("")
  const added = useMemo(
    () => new Set((view.data?.selections ?? []).map((item) => item.server_id)),
    [view.data]
  )
  const servers = useMemo(() => {
    const query = search.trim().toLowerCase()
    return (view.data?.servers ?? [])
      .filter((server) => !added.has(server.id))
      .filter(
        (server) =>
          !query ||
          server.name.toLowerCase().includes(query) ||
          server.upstream_url.toLowerCase().includes(query)
      )
      .sort((a, b) => Number(b.connected) - Number(a.connected))
  }, [view.data, search, added])

  return (
    <section
      aria-label="Add a LangSmith Managed Tool"
      className="space-y-3 rounded-md border p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-sm font-medium">
          Add a <ManagedBadge />
        </p>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
      </div>
      {view.isLoading ? (
        <p className="text-xs text-muted-foreground">Loading servers…</p>
      ) : view.isError ? (
        <p role="alert" className="text-xs text-destructive">
          {view.error.message}
        </p>
      ) : !view.data?.langsmith_connected ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Connect LangSmith to browse its managed MCP servers and reuse the
            connections you already made there.
          </p>
          <ConnectLangSmithButton />
        </div>
      ) : (
        <>
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search servers"
            aria-label="Search LangSmith Managed Tools"
            autoFocus
          />
          {servers.length === 0 ? (
            <p className="text-xs text-muted-foreground">No servers match.</p>
          ) : (
            <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border">
              {servers.map((server) => (
                <PickerRow
                  key={server.id}
                  server={server}
                  onConnect={popup.start}
                  connecting={popup.connecting === server.id}
                  onAdded={onClose}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}
