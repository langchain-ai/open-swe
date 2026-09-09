import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { IoLogoSlack } from "react-icons/io5"
import { SiNotion } from "react-icons/si"

import type { SessionUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { api, connectService } from "@/lib/api"
import { cn } from "@/lib/utils"

type SetError = (message: string | null) => void

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
  const mapping = useQuery({ queryKey: ["myMapping"], queryFn: api.myMapping })
  const [connecting, setConnecting] = useState(false)

  const slackUserId = mapping.data?.slack_user_id ?? null
  const workEmail = mapping.data?.work_email ?? null
  const connected = !!slackUserId

  const connect = () => {
    setConnecting(true)
    // Refresh the cached mapping when the user returns from the OAuth redirect.
    void qc.invalidateQueries({ queryKey: ["myMapping"] })
    void connectService("slack")?.finally(() => {
      setConnecting(false)
      void qc.invalidateQueries({ queryKey: ["myMapping"] })
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
              disabled={connecting || mapping.isLoading}
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

function NotionRow({ setError }: { setError: SetError }) {
  const qc = useQueryClient()
  const creds = useQuery({
    queryKey: ["myNotion"],
    queryFn: api.getMyNotionStatus,
  })
  const [connecting, setConnecting] = useState(false)

  const disconnect = useMutation({
    mutationFn: () => api.disconnectNotion(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["myNotion"] })
      setError(null)
    },
    onError: (e: Error) => setError(e.message),
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
              disabled={disconnect.isPending}
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
  const [error, setError] = useState<string | null>(null)

  return (
    <SettingsSection
      title="Personal connections"
      description="Accounts and credentials Open SWE can use on your behalf. Workspace MCP tools configured by an admin are shared with everyone."
    >
      <SlackRow user={user} />
      <NotionRow setError={setError} />
      {error && <p className="px-4 py-2 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
