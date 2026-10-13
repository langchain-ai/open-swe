import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type { LangSmithConnectionStatus, SessionUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api, connectService } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"

function StatusPill({ connected }: { connected: boolean }) {
  return (
    <Badge color={connected ? "primary" : "secondary"} size="xs">
      {connected ? "Connected" : "Not connected"}
    </Badge>
  )
}

function SlackRow({ user }: { user: SessionUser }) {
  const qc = useQueryClient()
  const [connecting, setConnecting] = useState(false)

  const slackUserId = user.slack_user_id ?? null
  const workEmail = user.email ?? null
  const connected = !!slackUserId

  const connect = () => {
    setConnecting(true)
    // The link lands on the session's user row; refresh it when the OAuth redirect returns.
    void qc.invalidateQueries({ queryKey: ["session"] })
    void connectService("slack")?.finally(() => {
      setConnecting(false)
      void qc.invalidateQueries({ queryKey: ["session"] })
    })
  }

  return (
    <SettingsRow
      label="Slack"
      description={
        connected
          ? `Linked to Slack member ${slackUserId}${workEmail ? ` · ${workEmail}` : ""}.`
          : "Sign in with Slack so Open SWE resolves your GitHub account when you tag it — the verified email also resolves Linear mentions."
      }
      control={
        <div className="flex items-center gap-space-2">
          <StatusPill connected={connected} />
          {user.slack_oauth_enabled ? (
            <Button
              size="xs"
              color={connected ? "secondary" : "primary"}
              variant={connected ? "outlined" : "normal"}
              leftDecorator={SlackLogoIcon}
              onClick={connect}
              disabled={connecting}
            >
              {connecting
                ? "Redirecting…"
                : connected
                  ? "Reconnect"
                  : "Connect"}
            </Button>
          ) : (
            <span className="text-xxs text-secondary">
              Sign in with Slack unavailable
            </span>
          )}
        </div>
      }
    />
  )
}

export const LANGSMITH_CONNECTION_KEY = ["myLangSmith"]

/** The caller's own LangSmith connection, shared by every feature that calls LangSmith as them. */
export function useLangSmithConnection() {
  return useQuery({
    queryKey: LANGSMITH_CONNECTION_KEY,
    queryFn: api.getMyLangSmithStatus,
  })
}

export function ConnectLangSmithButton({
  size = "xs",
}: {
  size?: "xs" | "sm"
}) {
  const qc = useQueryClient()
  const status = useLangSmithConnection()
  const [connecting, setConnecting] = useState(false)
  return (
    <Button
      size={size}
      color="primary"
      onClick={() => {
        setConnecting(true)
        void connectService("langsmith", window.location.href)?.finally(() => {
          setConnecting(false)
          void qc.invalidateQueries({ queryKey: LANGSMITH_CONNECTION_KEY })
        })
      }}
      disabled={connecting || status.isLoading}
    >
      {connecting ? "Redirecting…" : "Connect LangSmith"}
    </Button>
  )
}

function LangSmithRow() {
  const qc = useQueryClient()
  const status = useLangSmithConnection()
  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect LangSmith" },
    mutationFn: () => api.disconnectLangSmith(),
    onMutate: async () => ({
      undo: await optimisticUpdate<LangSmithConnectionStatus>(
        qc,
        LANGSMITH_CONNECTION_KEY,
        (current) => ({ ...current, connected: false, email: null })
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: LANGSMITH_CONNECTION_KEY })
      void qc.invalidateQueries({ queryKey: ["myManagedTools"] })
    },
  })
  if (!status.data?.available) return null
  const connected = status.data.connected
  return (
    <SettingsRow
      label="LangSmith"
      description={
        connected
          ? `Signed in${status.data.email ? ` as ${status.data.email}` : ""}. Open SWE can call LangSmith as you in your private threads.`
          : "Sign in with LangSmith so Open SWE can call LangSmith as you in your private threads."
      }
      control={
        <div className="flex items-center gap-space-2">
          <StatusPill connected={connected} />
          {connected ? (
            <Button
              color="secondary"
              variant="outlined"
              size="xs"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              Disconnect
            </Button>
          ) : (
            <ConnectLangSmithButton />
          )}
        </div>
      }
    />
  )
}

export function ConnectionsSection({ user }: { user: SessionUser }) {
  return (
    <SettingsSection title="Accounts">
      <SlackRow user={user} />
      <LangSmithRow />
    </SettingsSection>
  )
}
