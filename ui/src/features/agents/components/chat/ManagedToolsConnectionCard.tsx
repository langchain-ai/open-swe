import { Button } from "@langchain/macaw-components/Button"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { StatusBadge } from "@langchain/macaw-components/StatusBadge"
import type { ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { LockSimpleIcon } from "@phosphor-icons/react/dist/ssr/LockSimple"

import { LogoMark } from "@/components/Branding"
import { ConnectorLogo } from "@/components/ConnectorLogo"
import { SignInWithLangSmithButton } from "@/features/settings/components/ConnectionsSection"
import {
  ConnectButton,
  MANAGED_TOOLS_KEY,
  managedToolsQuery,
  useConnectPopup,
} from "@/features/settings/components/ManagedToolsSection"
import { api, managedToolConnectUrl } from "@/lib/api"
import type {
  ConnectableServer,
  ManagedServer,
  NewManagedServer,
} from "@/lib/api"
import type { ManagedToolsCardOffer } from "@/features/agents/lib/managedToolsCard"

const SIGN_IN: Record<NewManagedServer["kind"], string> = {
  oauth: "Signs you in on its own consent page",
  secret: "Uses an API key you enter in LangSmith",
  none: "Public, no sign-in",
}

/**
 * A managed tools card in a thread: the services `connect_managed_tools` offered to
 * connect, or the server `add_managed_tools_server` proposed, which is added only when
 * the person confirms here. The thread continues once its services work.
 */
export function ManagedToolsConnectionCard({
  cardId,
  offer: offered,
}: {
  cardId: string
  offer: ManagedToolsCardOffer
}) {
  const { threadId } = useParams({ strict: false })
  const qc = useQueryClient()
  const view = useQuery({
    ...managedToolsQuery,
    refetchOnWindowFocus: "always",
  })
  const add = useMutation({
    meta: { errorTitle: "Couldn't add the server" },
    mutationFn: api.addProposedManagedServer,
    onSuccess: () => qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY }),
  })
  // A confirmed proposal is one of the person's own servers, which the card then offers
  // to connect like any other. The stored card says which, so a reload finds it again.
  const card = threadId ? { threadId, cardId } : null
  const confirmed = useQuery({
    queryKey: ["managedToolsProposal", threadId, cardId],
    queryFn: () =>
      threadId ? api.getProposedManagedServer({ threadId, cardId }) : null,
    enabled: offered.status === "confirmation_required" && card !== null,
  })
  const added = add.data ?? confirmed.data ?? undefined
  const offer: ManagedToolsCardOffer =
    offered.status === "confirmation_required" && added
      ? {
          status: "connection_required",
          missing: [added],
          continuesAutomatically: added.kind !== "secret",
        }
      : offered
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

  if (offer.status === "confirmation_required") {
    const { server } = offer
    return (
      <Card
        title={`Add ${server.name}?`}
        subtitle="Robin proposes adding this MCP server to your managed tools. Nothing is added until you confirm."
      >
        <dl className="mt-space-4 flex flex-col gap-space-1 rounded-lg bg-surface-level-1 px-space-3 py-space-2 text-xs">
          <dt className="sr-only">URL</dt>
          <dd className="font-mono break-all text-primary">{server.url}</dd>
          <dt className="sr-only">Sign-in</dt>
          <dd className="text-secondary">{SIGN_IN[server.kind]}</dd>
        </dl>
        <div className="mt-space-4 flex items-center justify-between gap-space-3">
          <p className="text-xxs text-tertiary">
            Only add a server you trust: it sees what Robin sends it, and its
            tools reach your private threads.
          </p>
          <Button
            size="sm"
            className="shrink-0"
            loading={add.isPending}
            disabled={add.isPending || confirmed.isLoading || card === null}
            onClick={() => card && add.mutate(card)}
          >
            Add server
          </Button>
        </div>
      </Card>
    )
  }

  const live = view.data?.servers
  const rows = offer.missing.map((server) => ({
    server,
    current: live?.find((own) => own.id === server.id),
  }))
  const done = rows.every(({ current }) => current?.connected === true)
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
        {rows.map(({ server, current }) => (
          <ServiceRow
            key={server.id}
            server={server}
            current={current}
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
  current,
  href,
  connecting,
  onConnect,
}: {
  server: ConnectableServer
  current?: ManagedServer
  href: string
  connecting: boolean
  onConnect: (serverId: string) => void
}) {
  return (
    <li className="flex items-center gap-space-3 rounded-lg bg-surface-level-1 px-space-3 py-space-2">
      <ConnectorLogo name={server.name} icons={current} className="size-7" />
      <span className="min-w-0 flex-1 truncate font-medium text-primary">
        {server.name}
      </span>
      {current?.connected ? (
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
          look="primary"
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
          className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-level-2"
        >
          <LogoMark alt="" draggable={false} className="size-5" />
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
