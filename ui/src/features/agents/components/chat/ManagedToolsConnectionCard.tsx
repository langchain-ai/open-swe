import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { useParams } from "@tanstack/react-router"
import { Check, PlugZap } from "lucide-react"

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
        <p className="text-muted-foreground">
          Managed tools run with your own LangSmith account. Connect LangSmith,
          then ask again to connect the services they need.
        </p>
        <div className="mt-4 flex justify-end">
          <ConnectLangSmithButton />
        </div>
      </Card>
    )
  }

  const live = view.data?.gateways.find(
    (status) => status.gateway.id === offer.gateway.id
  )
  const missing = live ? live.missing : offer.missing
  return (
    <Card title={`${offer.gateway.name} managed tools`}>
      {live?.ready ? (
        <p className="flex items-center gap-1 text-primary">
          <Check aria-hidden className="size-3.5" /> Every service is connected.
          This thread continues on its own.
        </p>
      ) : (
        <>
          <p className="text-muted-foreground">
            Connect these to your account. LangSmith offers the tools once all
            of them are connected, and this thread then continues on its own.
          </p>
          <ul className="mt-3 divide-y divide-border rounded-md border">
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
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
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
      className="my-2 max-w-lg rounded-xl border border-border bg-card p-4 text-sm"
    >
      <div className="mb-2 flex items-center gap-2 font-medium">
        <PlugZap aria-hidden className="size-4" />
        {title}
      </div>
      {children}
    </section>
  )
}
