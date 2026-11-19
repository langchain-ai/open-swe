import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryKey } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Text } from "@langchain/macaw-components/Text"
import { PlusIcon } from "@phosphor-icons/react/dist/ssr/Plus"

import {
  SettingsRow,
  SettingsRowsSkeleton,
  SettingsSection,
} from "@/components/AppShell"
import { api, managedToolConnectUrl } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import type {
  BotToolsView,
  ConnectableServer,
  ManagedConnectResult,
  ManagedServer,
  ManagedToolsView,
  NewManagedServer,
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

/** Deletes a server the caller added themselves; its row goes at once. */
function useRemoveServer() {
  const qc = useQueryClient()
  const remove = useMutation({
    meta: { errorTitle: "Couldn't remove the server" },
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

function ServerTile({
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
  onRemove: (serverId: string) => void
}) {
  return (
    <div className="flex h-full min-w-0 flex-col justify-between gap-space-2 p-space-3">
      <Text as="span" variant="md" weight="medium" className="break-words">
        {server.name}
      </Text>
      <Text as="span" variant="xs" color="secondary">
        {AUTH_LABELS[server.kind]}
      </Text>
      <div className="flex flex-wrap items-center gap-space-2">
        <Badge color={server.connected ? "primary" : "secondary"} size="xs">
          {server.connected ? "Connected" : "Not connected"}
        </Badge>
        {server.connected && server.has_credential ? (
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            aria-label={`Disconnect ${server.name}`}
            onClick={() => onDisconnect(server.id)}
          >
            Disconnect
          </Button>
        ) : !server.connected && server.kind !== "none" ? (
          <ConnectButton
            server={server}
            href={managedToolConnectUrl(server.id)}
            label={server.kind === "secret" ? "Add API key" : undefined}
            connecting={connecting}
            onConnect={onConnect}
          />
        ) : null}
        {server.personal ? (
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            aria-label={`Remove ${server.name}`}
            onClick={() => onRemove(server.id)}
          >
            Remove
          </Button>
        ) : null}
      </div>
    </div>
  )
}

const AUTH_LABELS: Record<ManagedServer["kind"], string> = {
  oauth: "Sign-in (OAuth)",
  secret: "API key",
  none: "No sign-in required",
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

/** Registers an MCP server only the caller sees; it then lists here to connect. */
function AddServerDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [server, setServer] = useState(EMPTY_SERVER)
  const add = useMutation({
    mutationFn: api.addManagedServer,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY })
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
        title="Add MCP server"
        description="Only you can see and use a server you add. Your private threads load its tools; any sign-in or API key is stored in LangSmith."
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
            onChange={(name) => setServer({ ...server, name })}
          />
          <Input
            size="sm"
            label="Server URL"
            value={server.url}
            placeholder="https://mcp.example.com/mcp"
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
              hintText="The key itself is entered in LangSmith after you add the server."
              value={server.header ?? ""}
              placeholder="Authorization"
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
              Add server
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** The person's own LangSmith Managed Tools services, connected or connectable. */
export function ManagedToolsSection() {
  const view = useQuery(managedToolsQuery)
  const popup = useConnectPopup(MANAGED_TOOLS_KEY, (serverId) =>
    api.connectManagedTool(serverId)
  )
  const disconnect = useDisconnect()
  const remove = useRemoveServer()
  const [adding, setAdding] = useState(false)

  if (view.isPending) {
    return (
      <SettingsSection title="Managed Tools">
        <SettingsRowsSkeleton rows={3} label="Loading tools" />
      </SettingsSection>
    )
  }
  if (view.isError && !view.data) {
    return (
      <SettingsSection title="Managed Tools">
        <SettingsRow
          label="Couldn't load your tools"
          control={
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              loading={view.isFetching}
              onClick={() => void view.refetch()}
            >
              Retry
            </Button>
          }
        />
      </SettingsSection>
    )
  }
  if (!view.data?.configured) return null
  if (!view.data.langsmith_connected) {
    return (
      <SettingsSection title="Managed Tools">
        <SettingsRow
          label="LangSmith"
          description="Your LangSmith sign-in expired. Sign in again to use your tools."
          control={<SignInWithLangSmithButton />}
        />
      </SettingsSection>
    )
  }

  const sorted = [...view.data.servers].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
  )
  const catalog = sorted.filter((server) => !server.personal)
  const custom = sorted.filter((server) => server.personal)
  const groups = [
    {
      id: "available-tools",
      title: "Available Tools",
      description: "LangSmith Managed Tools for your private threads.",
      empty: "No tools are available.",
      servers: catalog,
    },
    {
      id: "custom-mcp-servers",
      title: "Custom MCP Servers",
      description: "Servers you add and only you can use in private threads.",
      empty: "You haven't added any MCP servers yet.",
      servers: custom,
      control: (
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          leftDecorator={PlusIcon}
          onClick={() => setAdding(true)}
        >
          Add MCP server
        </Button>
      ),
    },
  ]

  return (
    <div className="flex flex-col gap-space-6">
      {groups.map(({ id, title, description, control, empty, servers }) => (
        <section
          key={id}
          aria-labelledby={id}
          className="flex flex-col gap-space-3"
        >
          <div className="flex flex-wrap items-start justify-between gap-space-3">
            <div className="flex flex-col gap-space-1">
              <Text as="h2" id={id} variant="h5" weight="medium">
                {title}
              </Text>
              <Text as="p" variant="sm" color="secondary">
                {description}
              </Text>
            </div>
            {control}
          </div>
          {servers.length ? (
            <Card className="overflow-hidden p-0">
              <ul className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,13.5rem),1fr))] gap-px">
                {servers.map((server) => (
                  <li
                    key={server.id}
                    className="min-w-0 outline-1 outline-(--border-muted)"
                  >
                    <ServerTile
                      server={server}
                      connecting={popup.connecting === server.id}
                      onConnect={popup.start}
                      onDisconnect={disconnect}
                      onRemove={remove}
                    />
                  </li>
                ))}
              </ul>
            </Card>
          ) : (
            <Text as="p" role="status" variant="sm" color="secondary">
              {empty}
            </Text>
          )}
        </section>
      ))}
      {adding && <AddServerDialog onClose={() => setAdding(false)} />}
    </div>
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
