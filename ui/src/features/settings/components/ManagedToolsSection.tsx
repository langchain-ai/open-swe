import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import type {
  ManagedToolsConnectCard,
  ManagedToolsGatewayStatus,
  ManagedToolsMissingCredential,
} from "@/lib/api"
import { ConnectLangSmithButton } from "./ConnectionsSection"

export const MANAGED_TOOLS_KEY = ["myManagedTools"]

/** Opens LMT's consent page in a popup and re-checks the gateways when the person returns. */
export function useConnectPopup(card?: ManagedToolsConnectCard) {
  const qc = useQueryClient()
  const pending = useRef(false)
  const [connecting, setConnecting] = useState<string | null>(null)

  useEffect(() => {
    const refresh = () => {
      if (!pending.current) return
      pending.current = false
      setConnecting(null)
      void qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY })
    }
    window.addEventListener("focus", refresh)
    return () => window.removeEventListener("focus", refresh)
  }, [qc])

  const connect = useMutation({
    meta: { errorTitle: "Couldn't open the connection page" },
    mutationFn: async ({
      gatewayId,
      slug,
      popup,
    }: {
      gatewayId: string
      slug: string
      popup: Window
    }) => {
      const result = await api.connectManagedTool(gatewayId, slug, card)
      if (result.url) {
        pending.current = true
        popup.location.replace(result.url)
      } else {
        popup.close()
      }
      return result
    },
    onMutate: ({ gatewayId, slug }) => setConnecting(`${gatewayId}:${slug}`),
    onError: (_e, { popup }) => {
      popup.close()
      setConnecting(null)
    },
    onSuccess: (result) => {
      if (!result.connected) return
      setConnecting(null)
      void qc.invalidateQueries({ queryKey: MANAGED_TOOLS_KEY })
    },
  })

  return {
    connecting,
    start: (gatewayId: string, slug: string) => {
      // Open synchronously in the click so the browser does not block the popup.
      const popup = window.open("about:blank", "_blank")
      if (!popup) return
      popup.opener = null
      connect.mutate({ gatewayId, slug, popup })
    },
  }
}

export function MissingRow({
  gatewayId,
  credential,
  connecting,
  onConnect,
}: {
  gatewayId: string
  credential: ManagedToolsMissingCredential
  connecting: boolean
  onConnect: (gatewayId: string, slug: string) => void
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-3 py-2">
      <span className="text-sm text-primary">{credential.display_name}</span>
      {credential.kind === "secret" ? (
        <span className="text-xs text-secondary">
          Set its API key in LangSmith
        </span>
      ) : (
        <Button
          size="xs"
          color="secondary"
          variant="outlined"
          disabled={connecting}
          onClick={() => onConnect(gatewayId, credential.slug)}
        >
          {connecting ? "Waiting…" : "Connect"}
        </Button>
      )}
    </li>
  )
}

function GatewayStatus({
  status,
  connecting,
  onConnect,
}: {
  status: ManagedToolsGatewayStatus
  connecting: string | null
  onConnect: (gatewayId: string, slug: string) => void
}) {
  const workspaces = status.workspaces.join(", ")
  return (
    <section
      aria-label={`${status.gateway.name} managed tools`}
      className="rounded-md border border-default"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-primary">
            {status.gateway.name}
          </p>
          <p className="text-xs text-secondary">Used in {workspaces}</p>
        </div>
        {status.ready ? (
          <Badge color="success" size="xs">
            {`Ready · ${status.tool_count ?? status.gateway.tool_count} tools`}
          </Badge>
        ) : (
          <Badge color="warning" size="xs">
            {`Connect ${status.missing.length} to use`}
          </Badge>
        )}
      </div>
      {status.ready ? null : (
        <>
          <p className="px-3 pb-2 text-xs text-secondary">
            LangSmith offers this gateway&apos;s tools only after every service
            in it is connected to your account.
          </p>
          <ul className="divide-y divide-default border-t border-default">
            {status.missing.map((credential) => (
              <MissingRow
                key={credential.slug}
                gatewayId={status.gateway.id}
                credential={credential}
                connecting={
                  connecting === `${status.gateway.id}:${credential.slug}`
                }
                onConnect={onConnect}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/** The managed tools gateways an admin picked for workspaces, and what this person still needs. */
export function ManagedToolsSection() {
  const view = useQuery({
    queryKey: MANAGED_TOOLS_KEY,
    queryFn: api.getMyManagedTools,
  })
  const popup = useConnectPopup()

  if (!view.data?.configured && !view.isLoading) return null
  return (
    <SettingsSection
      title="Managed tools"
      description="Tools your workspace admins picked from LangSmith Managed Tools. They load in your private threads and run with your own connections; provider tokens stay in LangSmith."
    >
      {view.isLoading ? (
        <p className="p-4 text-xs text-secondary">Loading…</p>
      ) : view.isError ? (
        <p role="alert" className="p-4 text-xs text-error-secondary">
          {view.error.message}
        </p>
      ) : !view.data?.langsmith_connected ? (
        <SettingsRow
          label="LangSmith"
          description="Connect LangSmith to use the managed tools your workspaces offer."
          control={<ConnectLangSmithButton />}
        />
      ) : view.data.gateways.length === 0 ? (
        <p className="p-4 text-xs text-secondary">
          No workspace has picked a managed tools gateway yet.
        </p>
      ) : (
        <div className="space-y-3 p-4">
          {view.data.gateways.map((status) => (
            <GatewayStatus
              key={status.gateway.id}
              status={status}
              connecting={popup.connecting}
              onConnect={popup.start}
            />
          ))}
        </div>
      )}
    </SettingsSection>
  )
}
