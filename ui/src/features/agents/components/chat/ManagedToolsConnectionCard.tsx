import { CheckIcon } from "@langchain/macaw-components/icons"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { PlugsConnectedIcon } from "@phosphor-icons/react/dist/ssr/PlugsConnected"

import { ConnectLangSmithButton } from "@/features/settings/components/ConnectionsSection"
import {
  MANAGED_TOOLS_KEY,
  MissingRow,
  useConnectPopup,
} from "@/features/settings/components/ManagedToolsSection"
import type { ManagedToolsCardOffer } from "@/features/agents/lib/managedToolsCard"
import { api } from "@/lib/api"

/** The connect card `connect_managed_tools` offered; the thread continues once all connect. */
export function ManagedToolsConnectionCard({
  cardId,
  offer,
}: {
  cardId: string
  offer: ManagedToolsCardOffer
}) {
  const { threadId } = useParams({ strict: false })
  const view = useQuery({
    queryKey: MANAGED_TOOLS_KEY,
    queryFn: api.getMyManagedTools,
    refetchOnWindowFocus: "always",
  })
  const popup = useConnectPopup(threadId ? { threadId, cardId } : undefined)

  if (offer.status === "langsmith_required") {
    return (
      <Card title="Managed tools">
        <p className="text-secondary">
          Managed tools run with your own LangSmith account. Connect LangSmith,
          then ask again to connect the services they need.
        </p>
        <div className="mt-space-4 flex justify-end">
          <ConnectLangSmithButton />
        </div>
      </Card>
    )
  }

  const live = view.data?.gateways.find(
    (status) => status.gateway.id === offer.gateway.id
  )
  const missing = live ? live.missing : offer.missing
  // Only consent links can be waited on; API keys are set in LangSmith.
  const then = offer.missing.every((credential) => credential.kind === "oauth")
    ? "This thread then continues on its own."
    : "Then ask the agent to continue."
  return (
    <Card title={`${offer.gateway.name} managed tools`}>
      {live?.ready ? (
        <p className="flex items-center gap-space-1 text-success-secondary">
          <CheckIcon aria-hidden size={14} weight="bold" /> Every service is
          connected. {then}
        </p>
      ) : (
        <>
          <p className="text-secondary">
            Connect these to your account. LangSmith offers the tools once all
            of them are connected. {then}
          </p>
          <ul className="mt-space-3 divide-y divide-default rounded-md border border-default">
            {missing.map((credential) => (
              <MissingRow
                key={credential.slug}
                gatewayId={offer.gateway.id}
                credential={credential}
                connecting={
                  popup.connecting === `${offer.gateway.id}:${credential.slug}`
                }
                onConnect={popup.start}
              />
            ))}
          </ul>
        </>
      )}
      <p className="mt-space-3 text-xxs leading-relaxed text-tertiary">
        For your account only. Consent opens in a new tab; provider tokens stay
        in LangSmith.
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
