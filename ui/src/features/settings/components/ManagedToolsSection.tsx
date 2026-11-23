import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryKey } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Text } from "@langchain/macaw-components/Text"
import { CaretLeftIcon } from "@phosphor-icons/react/dist/ssr/CaretLeft"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { CircleIcon } from "@phosphor-icons/react/dist/ssr/Circle"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { LinkIcon } from "@phosphor-icons/react/dist/ssr/Link"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { PlusIcon } from "@phosphor-icons/react/dist/ssr/Plus"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import type { ReactNode } from "react"

import {
  SettingsRow,
  SettingsRowsSkeleton,
  SettingsSection,
} from "@/components/AppShell"
import { ConnectorLogo } from "@/components/ConnectorLogo"
import { ConfirmDialog } from "@/components/ConfirmDialog"
import { DropdownMenuItem } from "@/components/MenuItem"
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
      className={cn(look === "badge" && "h-auto rounded-full border-0 p-0")}
      aria-label={`${action} ${server.name}`}
      disabled={connecting}
      onClick={() => onConnect(server.id)}
    >
      {look === "badge" ? (
        <Badge
          size="md"
          color="primary"
          leftDecorator={LinkIcon}
          className="whitespace-nowrap"
        >
          {text}
        </Badge>
      ) : (
        text
      )}
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
    meta: { errorTitle: "Couldn't remove the tool" },
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

const PAGE_SIZE = 25

const AUTH_COLUMN_LABELS: Record<ManagedServer["kind"], string> = {
  none: "Public",
  oauth: "OAuth",
  secret: "API key",
}

function ServerRow({
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
  onRemove?: (serverId: string) => void
}) {
  const canDisconnect = server.connected && server.has_credential
  return (
    <tr className="border-b border-subtle last:border-b-0 hover:bg-surface-level-2">
      <td className="max-w-0 py-space-3 pr-space-3 pl-space-5">
        <div className="flex min-w-0 items-center gap-space-3">
          <ConnectorLogo name={server.name} icons={server} />
          <Text
            as="span"
            variant="sm"
            weight="medium"
            className="min-w-0 truncate"
          >
            {server.name}
          </Text>
        </div>
      </td>
      <td className="hidden w-28 py-space-3 pr-space-3 whitespace-nowrap sm:table-cell">
        <Text as="span" variant="sm" color="secondary">
          {AUTH_COLUMN_LABELS[server.kind]}
        </Text>
      </td>
      <td className="w-36 py-space-3 text-center">
        {!server.connected && server.kind !== "none" ? (
          <ConnectButton
            server={server}
            href={managedToolConnectUrl(server.id)}
            label="Connect"
            connecting={connecting}
            onConnect={onConnect}
            look="badge"
          />
        ) : (
          <Badge
            size="md"
            color={server.connected ? "success" : "secondary"}
            leftDecorator={server.connected ? CircleIcon : undefined}
            iconWeight="fill"
            className="gap-space-2 whitespace-nowrap [&>svg]:size-1.5"
          >
            {server.connected ? "Connected" : "Not connected"}
          </Badge>
        )}
      </td>
      <td className="w-14 py-space-3 pr-space-5 text-right">
        {canDisconnect || onRemove ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconButton
                icon={DotsThreeIcon}
                label={`Actions for ${server.name}`}
                size="sm"
                variant="plain"
                color="secondary"
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {canDisconnect ? (
                <DropdownMenuItem onSelect={() => onDisconnect(server.id)}>
                  Disconnect
                </DropdownMenuItem>
              ) : null}
              {onRemove ? (
                <DropdownMenuItem onSelect={() => onRemove(server.id)}>
                  Remove
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </td>
    </tr>
  )
}

/** The servers LangSmith lists for the caller, as LangSmith's Tools page shows them. */
function ServerTable({
  servers,
  empty,
  connecting,
  onConnect,
  onDisconnect,
  onRemove,
}: {
  servers: ManagedServer[]
  /** What the table says when nothing is listed and no search narrows it. */
  empty: string
  connecting: string | null
  onConnect: (serverId: string) => void
  onDisconnect: (serverId: string) => void
  /** Removes a server the caller added; its rows offer Remove when set. */
  onRemove?: (serverId: string) => void
}) {
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)

  const needle = query.trim().toLowerCase()
  const matches = servers
    .filter((server) => server.name.toLowerCase().includes(needle))
    .sort(
      (a, b) =>
        Number(b.connected) - Number(a.connected) ||
        a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
    )
  const pageCount = Math.max(1, Math.ceil(matches.length / PAGE_SIZE))
  const current = Math.min(page, pageCount)
  const start = (current - 1) * PAGE_SIZE
  const rows = matches.slice(start, start + PAGE_SIZE)
  const emptyMessage = needle ? `No tools match “${query.trim()}”.` : empty

  return (
    <div className="overflow-clip rounded-md border border-subtle">
      <div className="flex flex-wrap items-center gap-space-3 border-b border-subtle p-space-3">
        <Input
          size="sm"
          className="w-64 max-w-full"
          aria-label="Search tools"
          placeholder="Search tools"
          value={query}
          debounceMs={0}
          leftIcon={MagnifyingGlassIcon}
          rightAction={
            query
              ? {
                  icon: XIcon,
                  label: "Clear search",
                  onClick: () => {
                    setQuery("")
                    setPage(1)
                  },
                }
              : undefined
          }
          onChange={(value) => {
            setQuery(value)
            setPage(1)
          }}
        />
        <Text
          as="span"
          variant="sm"
          color="secondary"
          className="ml-auto whitespace-nowrap"
          aria-live="polite"
        >
          {matches.length} {matches.length === 1 ? "tool" : "tools"}
        </Text>
      </div>
      <table className="w-full table-fixed text-left">
        <thead className="bg-surface-level-2 text-xs text-tertiary">
          <tr className={cn(rows.length > 0 && "border-b border-subtle")}>
            <th
              scope="col"
              className="py-space-2 pr-space-3 pl-space-5 font-normal"
            >
              Name
            </th>
            <th
              scope="col"
              className="hidden w-28 py-space-2 pr-space-3 font-normal sm:table-cell"
            >
              Auth
            </th>
            <th scope="col" className="w-36 py-space-2 text-center font-normal">
              Status
            </th>
            <th scope="col" className="w-14 py-space-2 pr-space-5">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((server) => (
              <ServerRow
                key={server.id}
                server={server}
                connecting={connecting === server.id}
                onConnect={onConnect}
                onDisconnect={onDisconnect}
                onRemove={onRemove}
              />
            ))
          ) : (
            <tr>
              <td colSpan={4} className="h-32 text-center">
                <Text as="span" role="status" variant="sm" color="tertiary">
                  {emptyMessage}
                </Text>
              </td>
            </tr>
          )}
        </tbody>
      </table>
      <nav
        aria-label="Tools pagination"
        className="flex flex-wrap items-center justify-between gap-space-2 border-t border-subtle px-space-4 py-space-2"
      >
        <Text as="span" variant="sm" color="secondary">
          {matches.length
            ? `${start + 1}–${start + rows.length} of ${matches.length} tools`
            : "0 tools"}
        </Text>
        <div className="ml-auto flex items-center gap-space-2">
          <Text as="span" variant="sm" color="secondary">
            Page {current}
          </Text>
          <IconButton
            icon={CaretLeftIcon}
            label="Previous page"
            variant="outlined"
            color="secondary"
            size="sm"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
          />
          <IconButton
            icon={CaretRightIcon}
            label="Next page"
            variant="outlined"
            color="secondary"
            size="sm"
            disabled={current >= pageCount}
            onClick={() => setPage(current + 1)}
          />
        </div>
      </nav>
    </div>
  )
}

function ToolsGroup({
  id,
  title,
  control,
  children,
}: {
  id: string
  title: string
  control?: ReactNode
  children: ReactNode
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-space-3">
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <Text as="h2" id={id} variant="h5" weight="medium">
          {title}
        </Text>
        {control}
      </div>
      {children}
    </section>
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
        title="Add custom tool"
        description="Connect any remote MCP server. Only you can see and use a tool you add. Your private threads load it; any sign-in or API key is stored in LangSmith."
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
            label="MCP server URL"
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
              hintText="The key itself is entered in LangSmith after you add the tool."
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
              Add tool
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** The person's own LangSmith Managed Tools services, connected or connectable. */
export function ManagedToolsSection({ query = "" }: { query?: string }) {
  const view = useQuery(managedToolsQuery)
  const popup = useConnectPopup(MANAGED_TOOLS_KEY, (serverId) =>
    api.connectManagedTool(serverId)
  )
  const disconnect = useDisconnect()
  const remove = useRemoveServer()
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<ManagedServer | null>(null)

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

  const search = query.trim().toLowerCase()
  const listed = view.data.servers.filter((server) =>
    server.name.toLowerCase().includes(search)
  )
  const catalog = listed.filter((server) => !server.personal)
  const custom = listed.filter((server) => server.personal)

  return (
    <div className="flex flex-col gap-space-6">
      <ToolsGroup id="available-tools" title="Available Tools">
        <ServerTable
          servers={catalog}
          empty={
            search ? "No matching connections." : "No tools are available."
          }
          connecting={popup.connecting}
          onConnect={popup.start}
          onDisconnect={disconnect}
        />
      </ToolsGroup>
      <ToolsGroup
        id="custom-tools"
        title="Custom Tools"
        control={
          <Button
            size="sm"
            color="secondary"
            variant="outlined"
            leftDecorator={PlusIcon}
            onClick={() => setAdding(true)}
          >
            Add Custom Tool
          </Button>
        }
      >
        <ServerTable
          servers={custom}
          empty={
            search
              ? "No matching connections."
              : "You haven't added any custom tools yet."
          }
          connecting={popup.connecting}
          onConnect={popup.start}
          onDisconnect={disconnect}
          onRemove={(serverId) =>
            setRemoving(custom.find((server) => server.id === serverId) ?? null)
          }
        />
      </ToolsGroup>
      {adding && <AddServerDialog onClose={() => setAdding(false)} />}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={`Remove ${removing?.name ?? "this tool"}?`}
        description="It's deleted from LangSmith, and your private threads stop loading its tools. To use it again, add it again."
        confirmLabel="Remove"
        destructive
        onConfirm={() => {
          if (removing) remove(removing.id)
          setRemoving(null)
        }}
      />
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
