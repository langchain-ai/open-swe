import { Spinner } from "@langchain/macaw-components/Spinner"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"
import { PlugsConnectedIcon } from "@phosphor-icons/react/dist/ssr/PlugsConnected"

import { SignInWithLangSmithButton } from "@/features/settings/components/ConnectionsSection"
import {
  ConnectButton,
  MANAGED_TOOLS_KEY,
  managedToolsQuery,
  useConnectPopup,
} from "@/features/settings/components/ManagedToolsSection"
import { api, managedToolConnectUrl } from "@/lib/api"
import type { ConnectableServer } from "@/lib/api"
import type { ManagedToolsCardOffer } from "@/features/agents/lib/managedToolsCard"

/** The connect card `connect_managed_tools` offered; the thread continues once its services connect. */
export function ManagedToolsConnectionCard({
  cardId,
  offer,
}: {
  cardId: string
  offer: ManagedToolsCardOffer
}) {
  const { threadId } = useParams({ strict: false })
  const view = useQuery({
    ...managedToolsQuery,
    refetchOnWindowFocus: "always",
  })
  const popup = useConnectPopup(MANAGED_TOOLS_KEY, (serverId) =>
    api.connectManagedTool(
      serverId,
      threadId ? { threadId, cardId } : undefined
    )
  )

  if (offer.status === "langsmith_required") {
    return (
      <Card
        title="Sign in to LangSmith"
        subtitle="Your LangSmith sign-in expired. Sign in again, then ask again to connect the services you need."
        action={<SignInWithLangSmithButton size="sm" />}
      />
    )
  }

  const live = view.data?.servers
  const connected = (server: ConnectableServer) =>
    live?.some((own) => own.id === server.id && own.connected) ?? false
  const done = live !== undefined && offer.missing.every(connected)
  const names = offer.missing.map((server) => server.name).join(", ")
  return (
    <Card
      title={done ? `${names} connected` : `Connect ${names}`}
      subtitle={
        done
          ? offer.continuesAutomatically
            ? "Robin is picking this thread back up."
            : "Ask Robin to continue."
          : offer.continuesAutomatically
            ? "Robin needs your account to continue, and picks this thread back up once you connect."
            : "Robin needs your account to continue. Ask it to continue once you have connected."
      }
    >
      <ul className="mt-space-4 flex flex-col gap-space-2">
        {offer.missing.map((server) => (
          <ServiceRow
            key={server.id}
            server={server}
            connected={connected(server)}
            href={managedToolConnectUrl(
              server.id,
              threadId ? { threadId, cardId } : undefined
            )}
            connecting={popup.connecting === server.id}
            onConnect={popup.start}
          />
        ))}
      </ul>
      <p className="mt-space-4 flex items-center gap-space-1 text-xxs text-tertiary">
        <LockSimpleIcon aria-hidden size={12} />
        Only you can use these connections; provider tokens stay in LangSmith.
      </p>
    </Card>
  )
}

function ServiceRow({
  server,
  connected,
  href,
  connecting,
  onConnect,
}: {
  server: ConnectableServer
  connected: boolean
  href: string
  connecting: boolean
  onConnect: (serverId: string) => void
}) {
  return (
    <li className="flex items-center gap-space-3 rounded-lg bg-surface-level-1 px-space-3 py-space-2">
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center rounded-md bg-surface-level-3 text-xs font-medium text-secondary uppercase select-none"
      >
        {server.name.slice(0, 1)}
      </span>
      <span className="min-w-0 flex-1 truncate font-medium text-primary">
        {server.name}
      </span>
      {connected ? (
        <StatusBadge variant="success" size="sm">
          Connected
        </StatusBadge>
      ) : connecting ? (
        <span className="flex items-center gap-space-2 text-xs text-secondary">
          <Spinner size="xs" className="shrink-0" />
          Finish in the new tab
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline underline-offset-2"
          >
            Reopen
          </a>
        </span>
      ) : (
        <ConnectButton
          server={server}
          href={href}
          connecting={false}
          onConnect={onConnect}
          emphasis
        />
      )}
    </li>
  )
}

function Card({
  title,
  subtitle,
  action,
  children,
}: {
  title: string
  subtitle: string
  action?: ReactNode
  children?: ReactNode
}) {
  return (
    <section
      aria-label={title}
      className="my-space-2 max-w-lg rounded-xl border border-subtle bg-elevated p-space-4 text-sm shadow-sm dark:border-transparent"
    >
      <div className="flex items-start gap-space-3">
        <span
          aria-hidden
          className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-level-2 text-icon-secondary"
        >
          <PlugsConnectedIcon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-primary">{title}</p>
          <p className="mt-space-1 text-xs leading-relaxed text-secondary">
            {subtitle}
          </p>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
