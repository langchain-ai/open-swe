import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { IoLogoSlack } from "react-icons/io5"
import { SiNotion } from "react-icons/si"

import type { NotionCredentialStatus, SessionUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, connectService } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"

function StatusPill({ connected }: { connected: boolean }) {
  return (
    <Badge variant={connected ? "success" : "muted"}>
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

export function ConnectionsSection({ user }: { user: SessionUser }) {
  return (
    <SettingsSection
      title="Personal connections"
      description="Accounts and credentials Open SWE can use on your behalf. Workspace MCP tools configured by an admin are shared with everyone."
    >
      <SlackRow user={user} />
      <NotionRow />
    </SettingsSection>
  )
}
