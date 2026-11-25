import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryKey } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"

import {
  SettingsRow,
  SettingsRowsSkeleton,
  SettingsSection,
} from "@/components/AppShell"
import { ConnectorLogo } from "@/components/ConnectorLogo"
import { api, managedToolConnectUrl } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { cn } from "@/lib/utils"
import type {
  BotToolsView,
  ConnectableServer,
  ManagedConnectResult,
  ManagedServer,
  ManagedToolsView,
  NewManagedServer,
} from "@/lib/api"
import { SignInWithLangSmithButton } from "./ConnectionsSection"
import {
  CONNECT_BADGE_BUTTON,
  ConnectBadge,
  ConnectionRow,
  ConnectionStatusBadge,
} from "./ConnectionsTable"

export const MANAGED_TOOLS_KEY = ["myManagedTools"]

export const managedToolsQuery = {
  queryKey: MANAGED_TOOLS_KEY,
  queryFn: () => api.getMyManagedTools(),
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
  look = "outlined",
}: {
  server: Pick<ConnectableServer, "id" | "name"> &
    Partial<Pick<ConnectableServer, "kind">>
  /** Robin's connect route for the service, which the web follows as a link. */
  href: string
  label?: string
  connecting: boolean
  onConnect: (serverId: string) => void
  /** `primary` where it is the one thing to do, as on a thread's connect card; `badge` in a table's status column. */
  look?: "outlined" | "primary" | "badge"
}) {
  const action = label ?? (server.kind === "secret" ? "Set up" : "Connect")
  const text = connecting ? "Waiting…" : action
  return (
    <Button
      as={
        typeof window !== "undefined" && window.openSweDesktop ? undefined : (
          <a href={href} target="_blank" rel="noopener noreferrer" />
        )
      }
      size={look === "primary" ? "sm" : "xs"}
      color={look === "primary" ? "primary" : "secondary"}
      variant={
        look === "primary" ? "normal" : look === "badge" ? "plain" : "outlined"
      }
      className={cn(look === "badge" && CONNECT_BADGE_BUTTON)}
      aria-label={`${action} ${server.name}`}
      disabled={connecting}
      onClick={() => onConnect(server.id)}
    >
      {look === "badge" ? <ConnectBadge>{text}</ConnectBadge> : text}
    </Button>
  )
}

/** Removes the caller's own connection to one service; its row turns connectable at once. */
export function useDisconnectManagedConnection() {
  const qc = useQueryClient()
  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect the connection" },
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

/** Deletes a custom connection the caller added themselves; its row goes at once. */
export function useRemoveCustomConnection() {
  const qc = useQueryClient()
  const remove = useMutation({
    meta: { errorTitle: "Couldn't remove the connection" },
    mutationFn: api.removeManagedServer,
    onMutate: async (serverId) => ({
      undo: await optimisticUpdate<ManagedToolsView>(
        qc,
        MANAGED_TOOLS_KEY,
        (current) => ({
          ...current,
          servers: current.servers.filter((server) => server.id !== serverId),
        })
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY })
    },
  })
  return (serverId: string) => remove.mutate(serverId)
}

const AUTH_COLUMN_LABELS: Record<ManagedServer["kind"], string> = {
  none: "Public",
  oauth: "OAuth",
  secret: "API key",
}

export function ManagedServerRow({
  server,
  connecting,
  onConnect,
  onDisconnect,
  onRemove,
}: {
  server: ManagedServer
  connecting: boolean
  onConnect: (serverId: string) => void
  onDisconnect: (serverId: string) => void
  onRemove?: (server: ManagedServer) => void
}) {
  const canDisconnect = server.connected && server.has_credential
  const authLabel = AUTH_COLUMN_LABELS[server.kind]
  const detail = server.personal ? `Custom · ${authLabel}` : authLabel
  return (
    <ConnectionRow
      name={server.name}
      logo={<ConnectorLogo name={server.name} icons={server} />}
      detail={detail}
      showDetailInName={server.personal}
      status={
        !server.connected && server.kind !== "none" ? (
          <ConnectButton
            server={server}
            href={managedToolConnectUrl(server.id)}
            label="Connect"
            connecting={connecting}
            onConnect={onConnect}
            look="badge"
          />
        ) : (
          <ConnectionStatusBadge connected={server.connected} />
        )
      }
      actions={[
        ...(canDisconnect
          ? [{ label: "Disconnect", onSelect: () => onDisconnect(server.id) }]
          : []),
        ...(server.personal && onRemove
          ? [{ label: "Remove", onSelect: () => onRemove(server) }]
          : []),
      ]}
    />
  )
}

const SIGN_IN_TYPES: Array<{ value: NewManagedServer["kind"]; label: string }> =
  [
    { value: "oauth", label: "OAuth (sign in and approve access)" },
    { value: "secret", label: "API key (enter a key from the service)" },
    { value: "none", label: "None (no sign-in or key)" },
  ]

const EMPTY_SERVER: NewManagedServer = {
  name: "",
  url: "",
  kind: "oauth",
  header: "Authorization",
}

/** Registers a custom MCP connection only the caller sees; it then lists here to connect. */
export function AddConnectionDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void
  onAdded?: () => void
}) {
  const qc = useQueryClient()
  const [server, setServer] = useState(EMPTY_SERVER)
  const add = useMutation({
    mutationFn: api.addManagedServer,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY })
      onAdded?.()
      onClose()
    },
  })
  const ready =
    server.name.trim() !== "" &&
    server.url.trim().toLowerCase().startsWith("https://") &&
    (server.kind !== "secret" || (server.header ?? "").trim() !== "")
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        title="Add custom integration"
        description="Connect any remote MCP server. Only you can see and use an integration you add. Your private threads load it; any sign-in or API key is stored in LangSmith."
        className="w-[min(30rem,calc(100vw-2rem))]"
      >
        <form
          className="flex flex-col gap-space-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (ready) add.mutate(server)
          }}
        >
          <Input
            size="sm"
            label="Name"
            value={server.name}
            placeholder="Sentry"
            debounceMs={0}
            onChange={(name) => setServer({ ...server, name })}
          />
          <Input
            size="sm"
            label="MCP server URL"
            value={server.url}
            placeholder="https://mcp.example.com/mcp"
            debounceMs={0}
            onChange={(url) => setServer({ ...server, url })}
          />
          <div className="flex flex-col gap-space-1">
            <span className="text-xs font-medium text-primary">Sign-in</span>
            <Select
              aria-label="Sign-in"
              options={SIGN_IN_TYPES}
              value={server.kind}
              onChange={(kind) => kind && setServer({ ...server, kind })}
            />
          </div>
          {server.kind === "secret" ? (
            <Input
              size="sm"
              label="Header name"
              hintText="The key itself is entered in LangSmith after you add the connection."
              value={server.header ?? ""}
              placeholder="Authorization"
              debounceMs={0}
              onChange={(header) => setServer({ ...server, header })}
            />
          ) : null}
          {add.isError ? (
            <p role="alert" className="text-xs text-error-secondary">
              {add.error.message}
            </p>
          ) : null}
          <div className="flex justify-end gap-space-2">
            <Button
              type="button"
              color="secondary"
              variant="outlined"
              size="md"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              size="md"
              loading={add.isPending}
              disabled={!ready || add.isPending}
            >
              Add integration
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The services in Robin's bot kit, which runs outside private threads load: shared
 * threads, Slack channels and automations. Read-only: the bot's credentials live on its
 * LangSmith Managed Tools profile, which a LangSmith workspace admin manages.
 */
export function BotToolsSection() {
  const view = useQuery<BotToolsView>({
    queryKey: ["botManagedTools"],
    queryFn: api.getBotManagedTools,
  })

  if (!view.data?.configured && !view.isLoading && !view.isError) return null
  return (
    <SettingsSection
      title="Bot Managed Tools"
      description="The services in Robin's bot kit. Shared threads, Slack channels and automations use them with the API keys stored on the bot's LangSmith Managed Tools profile."
    >
      {view.isLoading ? (
        <SettingsRowsSkeleton rows={3} label="Loading managed tools" />
      ) : view.isError ? (
        <p role="alert" className="p-space-4 text-xs text-error-secondary">
          {view.error.message}
        </p>
      ) : !view.data?.langsmith_connected ? (
        <SettingsRow
          label="LangSmith"
          description="Sign in with LangSmith to see the bot's services; LangSmith shows a kit only to a person."
          control={<SignInWithLangSmithButton />}
        />
      ) : (
        <>
          {(view.data?.servers ?? []).length === 0 ? (
            <p className="p-space-4 text-xs text-secondary">No services yet.</p>
          ) : (
            (view.data?.servers ?? []).map((server) => (
              <SettingsRow
                key={server.id}
                label={server.name}
                leadingIcon={
                  <ConnectorLogo name={server.name} icons={server} />
                }
                description={
                  server.connected
                    ? undefined
                    : "No credential on the bot's profile; its tools don't load"
                }
                control={
                  server.connected ? (
                    <Badge color="primary" size="xs">
                      Connected
                    </Badge>
                  ) : (
                    <Badge color="secondary" size="xs">
                      Not connected
                    </Badge>
                  )
                }
              />
            ))
          )}
          <p className="p-space-4 text-xs text-secondary">
            The kit and the profile&apos;s credentials are managed in LangSmith.
          </p>
        </>
      )}
    </SettingsSection>
  )
}
