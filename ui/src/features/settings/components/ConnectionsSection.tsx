import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { IoLogoSlack } from "react-icons/io5"
import { SiNotion } from "react-icons/si"

import type {
  LangSmithConnectionStatus,
  NotionCredentialStatus,
  SessionUser,
} from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { api, connectService } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { cn } from "@/lib/utils"

function StatusPill({ connected }: { connected: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-medium",
        connected
          ? "bg-primary/10 text-primary"
          : "bg-muted text-muted-foreground"
      )}
    >
      {connected ? "Connected" : "Not connected"}
    </span>
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
        <div className="flex items-center gap-2">
          <StatusPill connected={connected} />
          {user.slack_oauth_enabled ? (
            <Button
              size="sm"
              variant={connected ? "outline" : "default"}
              onClick={connect}
              disabled={connecting}
            >
              <IoLogoSlack className="size-4" />
              {connecting
                ? "Redirecting…"
                : connected
                  ? "Reconnect"
                  : "Connect"}
            </Button>
          ) : (
            <span className="text-[10px] text-muted-foreground">
              Sign in with Slack unavailable
            </span>
          )}
        </div>
      }
    />
  )
}

function NotionRow() {
  const qc = useQueryClient()
  const creds = useQuery({
    queryKey: ["myNotion"],
    queryFn: api.getMyNotionStatus,
  })
  const [connecting, setConnecting] = useState(false)

  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect Notion" },
    mutationFn: () => api.disconnectNotion(),
    onMutate: async () => ({
      undo: await optimisticUpdate<NotionCredentialStatus>(
        qc,
        ["myNotion"],
        (current) => ({ ...current, connected: false })
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => qc.invalidateQueries({ queryKey: ["myNotion"] }),
  })

  const connected = !!creds.data?.connected
  const connect = () => {
    setConnecting(true)
    void qc.invalidateQueries({ queryKey: ["myNotion"] })
    void connectService("notion")?.finally(() => {
      setConnecting(false)
      void qc.invalidateQueries({ queryKey: ["myNotion"] })
    })
  }

  return (
    <SettingsRow
      label="Notion"
      description="Let agent runs use Notion MCP tools with your workspace permissions. OAuth tokens are encrypted at rest and scoped to your account."
      control={
        <div className="flex items-center gap-2">
          <StatusPill connected={connected} />
          {connected ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => disconnect.mutate()}
            >
              Disconnect
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={connect}
              disabled={connecting || creds.isLoading}
            >
              <SiNotion className="size-4" />
              {connecting ? "Redirecting…" : "Connect"}
            </Button>
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
  size = "sm",
}: {
  size?: "sm" | "default"
}) {
  const qc = useQueryClient()
  const status = useLangSmithConnection()
  const [connecting, setConnecting] = useState(false)
  return (
    <Button
      size={size}
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
    onSettled: () =>
      qc.invalidateQueries({ queryKey: LANGSMITH_CONNECTION_KEY }),
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
        <div className="flex items-center gap-2">
          <StatusPill connected={connected} />
          {connected ? (
            <Button
              variant="outline"
              size="sm"
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
      <NotionRow />
      <LangSmithRow />
    </SettingsSection>
  )
}
