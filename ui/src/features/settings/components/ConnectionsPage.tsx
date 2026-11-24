import { useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { Text } from "@langchain/macaw-components/Text"
import { CaretLeftIcon } from "@phosphor-icons/react/dist/ssr/CaretLeft"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { PlusIcon } from "@phosphor-icons/react/dist/ssr/Plus"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"

import { ConfirmDialog } from "@/components/ConfirmDialog"
import { api } from "@/lib/api"
import type { ManagedServer, SessionUser, SlackRecovery } from "@/lib/api"
import { useRegisterAppCommands } from "@/lib/appCommands"
import type { AppCommand } from "@/lib/appCommands"
import {
  GitHubRow,
  SignInWithLangSmithButton,
  SlackRow,
  readSlackRecoveryFromUrl,
} from "./ConnectionsSection"
import { ConnectionsTable } from "./ConnectionsTable"
import {
  AddConnectionDialog,
  MANAGED_TOOLS_KEY,
  ManagedServerRow,
  managedToolsQuery,
  useConnectPopup,
  useDisconnectManagedConnection,
  useRemoveCustomConnection,
} from "./ManagedToolsSection"
import { SlackRecoveryDialog } from "./SlackRecoveryDialog"

const PAGE_SIZE = 25

export function ConnectionsPage({ user }: { user: SessionUser }) {
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<ManagedServer | null>(null)
  const [recovery, setRecovery] = useState<SlackRecovery | null>(
    readSlackRecoveryFromUrl
  )
  const input = useRef<HTMLInputElement>(null)

  const commands = useMemo<ReadonlyArray<AppCommand>>(
    () => [
      {
        id: "connections-search",
        label: "Search connections",
        shortcuts: ["mod+f"],
        enableInFormFields: true,
        group: "Navigation",
        menu: "edit",
        run: () => {
          input.current?.focus()
          input.current?.select()
        },
      },
    ],
    []
  )
  useRegisterAppCommands(commands)

  const view = useQuery(managedToolsQuery)
  const popup = useConnectPopup(MANAGED_TOOLS_KEY, (serverId) =>
    api.connectManagedTool(serverId)
  )
  const disconnect = useDisconnectManagedConnection()
  const remove = useRemoveCustomConnection()

  const canAddCustom = Boolean(
    view.data?.configured && view.data.langsmith_connected
  )
  const needle = query.trim().toLowerCase()

  const coreIds: Array<"github" | "slack"> = [
    ...("github".includes(needle) ? (["github"] as const) : []),
    ...("slack".includes(needle) ? (["slack"] as const) : []),
  ]

  const managedServers = canAddCustom
    ? (view.data?.servers ?? [])
        .filter((server) => server.name.toLowerCase().includes(needle))
        .sort(
          (a, b) =>
            Number(b.personal) - Number(a.personal) ||
            Number(b.connected) - Number(a.connected) ||
            a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
        )
    : []

  const total = coreIds.length + managedServers.length
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const current = Math.min(page, pageCount)
  const start = (current - 1) * PAGE_SIZE
  const end = start + PAGE_SIZE
  const visibleCore = coreIds.slice(start, end)
  const visibleServers = managedServers.slice(
    Math.max(0, start - coreIds.length),
    Math.max(0, end - coreIds.length)
  )
  const visibleCount = visibleCore.length + visibleServers.length
  const emptyMessage = needle
    ? `No connections match “${query.trim()}”.`
    : "No connections are available."

  return (
    <div className="min-w-0 flex-1 overflow-y-auto text-primary">
      <div className="flex w-full max-w-6xl flex-col gap-space-6 px-space-5 pt-space-8 pb-space-9 max-md:pt-space-9">
        <header className="flex flex-wrap items-start justify-between gap-space-4">
          <div className="flex flex-col gap-space-1">
            <Text variant="h1">Connections</Text>
            <Text as="p" variant="sm" color="secondary">
              Manage the connections to let Robin work across your tools.
            </Text>
          </div>
          {canAddCustom ? (
            <Button leftDecorator={PlusIcon} onClick={() => setAdding(true)}>
              Add custom connection
            </Button>
          ) : null}
        </header>

        <div className="overflow-clip rounded-md border border-subtle">
          <div className="flex flex-wrap items-center gap-space-3 border-b border-subtle p-space-3">
            <Input
              ref={input}
              size="sm"
              className="w-64 max-w-full"
              aria-label="Search connections"
              placeholder="Search connections"
              leftIcon={MagnifyingGlassIcon}
              value={query}
              debounceMs={0}
              rightAction={
                query
                  ? {
                      icon: XIcon,
                      label: "Clear search",
                      onClick: () => {
                        setQuery("")
                        setPage(1)
                        input.current?.focus()
                      },
                    }
                  : undefined
              }
              onChange={(value) => {
                setQuery(value)
                setPage(1)
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault()
                  setQuery("")
                  setPage(1)
                }
              }}
            />
            <Text
              as="span"
              variant="sm"
              color="secondary"
              className="ml-auto whitespace-nowrap"
              aria-live="polite"
            >
              {total} {total === 1 ? "connection" : "connections"}
            </Text>
          </div>

          <ConnectionsTable detail="Details" empty={emptyMessage}>
            {visibleCore.map((id) =>
              id === "github" ? (
                <GitHubRow key="core:github" user={user} />
              ) : (
                <SlackRow
                  key="core:slack"
                  user={user}
                  onRecovery={setRecovery}
                />
              )
            )}
            {visibleServers.map((server) => (
              <ManagedServerRow
                key={server.id}
                server={server}
                connecting={popup.connecting === server.id}
                onConnect={popup.start}
                onDisconnect={disconnect}
                onRemove={setRemoving}
              />
            ))}
          </ConnectionsTable>

          {view.isPending ? (
            <div className="border-t border-subtle px-space-4 py-space-3">
              <Text as="span" role="status" variant="xs" color="secondary">
                Loading additional connections…
              </Text>
            </div>
          ) : view.isError && !view.data ? (
            <div className="flex flex-wrap items-center justify-between gap-space-3 border-t border-subtle px-space-4 py-space-3">
              <Text as="span" variant="xs" color="secondary">
                Couldn&apos;t load additional connections
              </Text>
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                loading={view.isFetching}
                onClick={() => void view.refetch()}
              >
                Retry
              </Button>
            </div>
          ) : view.data?.configured && !view.data.langsmith_connected ? (
            <div className="flex flex-wrap items-center justify-between gap-space-3 border-t border-subtle px-space-4 py-space-3">
              <Text as="span" variant="xs" color="secondary">
                Your LangSmith sign-in expired. Sign in again to use your
                connections.
              </Text>
              <SignInWithLangSmithButton />
            </div>
          ) : null}

          {pageCount > 1 ? (
            <nav
              aria-label="Connections pagination"
              className="flex flex-wrap items-center justify-between gap-space-2 border-t border-subtle px-space-4 py-space-2"
            >
              <Text as="span" variant="sm" color="secondary">
                {`${start + 1}–${start + visibleCount} of ${total} connections`}
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
          ) : null}
        </div>

        {recovery && (
          <SlackRecoveryDialog
            recovery={recovery}
            onCancel={() => setRecovery(null)}
          />
        )}
        {adding && (
          <AddConnectionDialog
            onClose={() => setAdding(false)}
            onAdded={() => {
              setQuery("")
              setPage(1)
            }}
          />
        )}
        <ConfirmDialog
          open={removing !== null}
          onOpenChange={(open) => !open && setRemoving(null)}
          title={`Remove ${removing?.name ?? "this connection"}?`}
          description="It's deleted from LangSmith, and your private threads stop using it. To use it again, add it again."
          confirmLabel="Remove"
          destructive
          onConfirm={() => {
            if (removing) remove(removing.id)
            setRemoving(null)
          }}
        />
      </div>
    </div>
  )
}
