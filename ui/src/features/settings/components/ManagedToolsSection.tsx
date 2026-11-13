import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryKey } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api, managedToolConnectUrl } from "@/lib/api"
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
 * Tracks a consent page the person opened and re-reads `queryKey` when they return.
 *
 * On the web the Connect button is a plain link to Robin's connect route, which mints the
 * consent link and redirects to it: every browser follows a link, while some (Arc among
 * them) lose the tab a script opened before it could be sent on. The desktop app has no
 * tabs, so there `start` mints the link with `open` and hands it to the person's browser.
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
    mutationFn: async (serverId: string) => {
      const result = await open(serverId)
      if (result.url) {
        pending.current = true
        if (!(await window.openSweDesktop?.openExternal(result.url))) {
          throw new Error("Couldn't open the connection page in your browser")
        }
      }
      return result
    },
    onMutate: (serverId) => setConnecting(serverId),
    onError: () => {
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
      if (window.openSweDesktop) {
        connect.mutate(serverId)
        return
      }
      // The button's link opens the page; wait for the person to come back.
      pending.current = true
      setConnecting(serverId)
    },
  }
}

export function ConnectButton({
  server,
  href,
  label,
  connecting,
  onConnect,
  emphasis = false,
}: {
  server: Pick<ConnectableServer, "id" | "name"> &
    Partial<Pick<ConnectableServer, "kind">>
  /** Robin's connect route for the service, which the web follows as a link. */
  href: string
  label?: string
  connecting: boolean
  onConnect: (serverId: string) => void
  /** The primary action where it is the one thing to do, as on a thread's connect card. */
  emphasis?: boolean
}) {
  const action = label ?? (server.kind === "secret" ? "Set up" : "Connect")
  return (
    <Button
      as={
        typeof window !== "undefined" && window.openSweDesktop ? undefined : (
          <a href={href} target="_blank" rel="noopener noreferrer" />
        )
      }
      size={emphasis ? "sm" : "xs"}
      color={emphasis ? "primary" : "secondary"}
      variant={emphasis ? "normal" : "outlined"}
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
            href={managedToolConnectUrl(server.id)}
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
  // Public workspace services work for everyone without connecting, so only list
  // the person's own servers and those they connected or could.
  const servers = (view.data?.servers ?? []).filter(
    (server) =>
      server.personal || server.has_credential || server.kind !== "none"
  )
  return (
    <SettingsSection
      title="Managed Tools"
      description="The services in Robin's personal LangSmith Managed Tools kit. Your private threads use them with your own accounts; provider tokens stay in LangSmith."
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
      ) : (
        <>
          {servers.length === 0 ? (
            <p className="p-space-4 text-xs text-secondary">No services yet.</p>
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
          <p className="px-space-4 pb-space-4 text-xs text-secondary">
            A LangSmith workspace admin adds services to the kit in LangSmith;
            they show up here to connect.
          </p>
        </>
      )}
    </SettingsSection>
  )
}
