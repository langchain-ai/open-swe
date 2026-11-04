import { CheckIcon } from "@langchain/macaw-components/icons"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { PlugsConnectedIcon } from "@phosphor-icons/react/dist/ssr/PlugsConnected"

import { SignInWithLangSmithButton } from "@/features/settings/components/ConnectionsSection"
import {
  ConnectButton,
  MANAGED_TOOLS_KEY,
  managedToolsQuery,
  useConnectPopup,
} from "@/features/settings/components/ManagedToolsSection"
import { api } from "@/lib/api"
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
      <Card title="Managed Tools">
        <p className="text-secondary">
          Managed tools run with your LangSmith sign-in, which expired. Sign in
          again, then ask again to connect the services they need.
        </p>
        <div className="mt-space-4 flex justify-end">
          <SignInWithLangSmithButton />
        </div>
      </Card>
    )
  }

  const live = view.data?.servers
  const missing = live
    ? offer.missing.filter(
        (server) => !live.some((own) => own.id === server.id && own.connected)
      )
    : offer.missing
  const then = offer.continuesAutomatically
    ? "This thread then continues on its own."
    : "Then ask the agent to continue."
  return (
    <Card title="Managed Tools">
      {live && missing.length === 0 ? (
        <p className="flex items-center gap-space-1 text-success-secondary">
          <CheckIcon aria-hidden size={14} weight="bold" /> Connected. {then}
        </p>
      ) : (
        <>
          <p className="text-secondary">
            Connect {missing.length === 1 ? "this" : "these"} to your account.{" "}
            {then}
          </p>
          <ul className="mt-space-3 divide-y divide-default rounded-md border border-default">
            {missing.map((server) => (
              <li
                key={server.id}
                className="flex flex-wrap items-center justify-between gap-space-3 px-space-3 py-space-2"
              >
                <span className="text-sm text-primary">{server.name}</span>
                <ConnectButton
                  server={server}
                  connecting={popup.connecting === server.id}
                  onConnect={popup.start}
                />
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="mt-space-3 text-xxs leading-relaxed text-tertiary">
        For your account only. Each page opens in a new tab; provider tokens
        stay in LangSmith.
      </p>
    </Card>
  )
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="my-space-2 max-w-lg rounded-lg border border-default bg-surface-level-2 p-space-4 text-sm text-primary"
    >
      <div className="mb-space-2 flex items-center gap-space-2 font-medium">
        <PlugsConnectedIcon
          aria-hidden
          size={16}
          weight="regular"
          className="text-icon-secondary"
        />
        {title}
      </div>
      {children}
    </section>
  )
}
