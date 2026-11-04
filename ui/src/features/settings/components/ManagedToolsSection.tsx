import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryKey } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import type {
  ConnectableServer,
  ManagedConnectResult,
  ManagedServer,
  ManagedToolsView,
} from "@/lib/api"
import { SignInWithLangSmithButton } from "./ConnectionsSection"

export const MANAGED_TOOLS_KEY = ["myManagedTools"]

export const managedToolsQuery = {
  queryKey: MANAGED_TOOLS_KEY,
  queryFn: api.getMyManagedTools,
}

/**
 * Opens a consent page from `open` in a popup and re-reads `queryKey` when the
 * person returns.
 */
export function useConnectPopup(
  queryKey: QueryKey,
  open: (serverId: string) => Promise<ManagedConnectResult>
) {
  const qc = useQueryClient()
  const pending = useRef(false)
  const [connecting, setConnecting] = useState<string | null>(null)

  useEffect(() => {
    const refresh = () => {
      if (!pending.current) return
      pending.current = false
      setConnecting(null)
      void qc.invalidateQueries({ queryKey })
    }
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [qc, queryKey])

  const connect = useMutation({
    meta: { errorTitle: "Couldn't open the connection page" },
    mutationFn: async ({
      serverId,
      popup,
    }: {
      serverId: string
      popup: Window | null
    }) => {
      const result = await open(serverId)
      if (result.url) {
        pending.current = true
        if (popup) {
          popup.location.replace(result.url)
        } else if (!(await window.openSweDesktop?.openExternal(result.url))) {
          throw new Error("Couldn't open the connection page in your browser")
        }
      } else {
        popup?.close()
      }
      return result
    },
    onMutate: ({ serverId }) => setConnecting(serverId),
    onError: (_e, { popup }) => {
      popup?.close()
      pending.current = false
      setConnecting(null)
    },
    onSuccess: (result) => {
      if (!result.connected) return
      setConnecting(null)
      void qc.invalidateQueries({ queryKey })
    },
  })

  return {
    connecting,
    start: (serverId: string) => {
      const popup = window.openSweDesktop
        ? null
        : window.open("about:blank", "_blank")
      if (!popup && !window.openSweDesktop) return
      if (popup) popup.opener = null
      connect.mutate({ serverId, popup })
    },
  }
}

export function ConnectButton({
  server,
  label,
  connecting,
  onConnect,
}: {
  server: Pick<ConnectableServer, "id" | "name"> &
    Partial<Pick<ConnectableServer, "kind">>
  label?: string
  connecting: boolean
  onConnect: (serverId: string) => void
}) {
  const action = label ?? (server.kind === "secret" ? "Set up" : "Connect")
  return (
    <Button
      size="xs"
      color="secondary"
      variant="outlined"
      aria-label={`${action} ${server.name}`}
      disabled={connecting}
      onClick={() => onConnect(server.id)}
    >
      {connecting ? "Waiting…" : action}
    </Button>
  )
}

/** Removes the caller's own connection to one service; its row turns connectable at once. */
function useDisconnect() {
  const qc = useQueryClient()
  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect the service" },
    mutationFn: api.disconnectManagedTool,
    onMutate: async (serverId) => ({
      undo: await optimisticUpdate<ManagedToolsView>(
        qc,
        MANAGED_TOOLS_KEY,
        (current) => ({
          ...current,
          servers: current.servers.map((server) =>
            server.id === serverId
              ? { ...server, connected: false, has_credential: false }
              : server
          ),
        })
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY })
    },
  })
  return (serverId: string) => disconnect.mutate(serverId)
}

function ServerRow({
  server,
  connecting,
  onConnect,
  onDisconnect,
}: {
  server: ManagedServer
  connecting: boolean
  onConnect: (serverId: string) => void
  onDisconnect: (serverId: string) => void
}) {
  return (
    <SettingsRow
      label={server.name}
      description={
        server.connected
          ? undefined
          : server.kind === "secret"
            ? "Needs your API key"
            : "Not connected"
      }
      control={
        server.connected ? (
          <div className="flex items-center gap-space-2">
            <Badge color="primary" size="xs">
              Connected
            </Badge>
            {server.has_credential ? (
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                aria-label={`Disconnect ${server.name}`}
                onClick={() => onDisconnect(server.id)}
              >
                Disconnect
              </Button>
            ) : null}
          </div>
        ) : (
          <ConnectButton
            server={server}
            connecting={connecting}
            onConnect={onConnect}
          />
        )
      }
    />
  )
}

/** The person's own LangSmith Managed Tools services, connected or connectable. */
export function ManagedToolsSection() {
  const view = useQuery(managedToolsQuery)
  const popup = useConnectPopup(MANAGED_TOOLS_KEY, (serverId) =>
    api.connectManagedTool(serverId)
  )
  const disconnect = useDisconnect()

  if (!view.data?.configured && !view.isLoading) return null
  // Public services with nothing to connect are always usable, so only list
  // what the person connected or could.
  const servers = (view.data?.servers ?? []).filter(
    (server) => server.connected || server.kind !== "none"
  )
  return (
    <SettingsSection
      title="Managed Tools"
      description="Your LangSmith Managed Tools services. Connected ones load in your private threads with your own accounts, in place of the workspace bot's service of the same name; provider tokens stay in LangSmith."
    >
      {view.isLoading ? (
        <p className="p-space-4 text-xs text-secondary">Loading…</p>
      ) : view.isError ? (
        <p role="alert" className="p-space-4 text-xs text-error-secondary">
          {view.error.message}
        </p>
      ) : !view.data?.langsmith_connected ? (
        <SettingsRow
          label="LangSmith"
          description="Your LangSmith sign-in expired. Sign in again to use your managed tools."
          control={<SignInWithLangSmithButton />}
        />
      ) : servers.length === 0 ? (
        <p className="p-space-4 text-xs text-secondary">
          No services yet. Add them in LangSmith under Settings &gt; Tools.
        </p>
      ) : (
        servers.map((server) => (
          <ServerRow
            key={server.id}
            server={server}
            connecting={popup.connecting === server.id}
            onConnect={popup.start}
            onDisconnect={disconnect}
          />
        ))
      )}
    </SettingsSection>
  )
}
