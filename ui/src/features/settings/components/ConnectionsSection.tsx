import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import type { ReactNode } from "react"

import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { ProviderMark } from "@langchain/gtm-platform-design-system/patterns/provider-mark"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"

import { LogOut } from "@/components/glyphs"
import type {
  LangSmithConnectionStatus,
  NotionCredentialStatus,
  SessionUser,
} from "@/lib/api"
import { api, connectLangSmith, connectService } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"

/** One linked account: its mark, name and state, what it is for, and its one action. */
function ConnectionRow({
  provider,
  name,
  connected,
  description,
  action,
}: {
  provider: string
  name: string
  connected: boolean
  description: string
  action: ReactNode
}) {
  return (
    <Inline
      gap="md"
      align="center"
      justify="between"
      wrap
      className="border-b border-line px-5 py-3 last:border-b-0"
    >
      <Inline gap="md" align="center" className="min-w-0 flex-1">
        <ProviderMark provider={provider} label={name} />
        <Stack gap="xs" className="min-w-0 flex-1">
          <Inline gap="sm" align="center" wrap>
            <Box render={<span />} className="text-label font-medium text-ink">
              {name}
            </Box>
            <Badge tier="quiet" tone={connected ? "positive" : "neutral"} dot>
              {connected ? "Connected" : "Not connected"}
            </Badge>
          </Inline>
          <Box render={<p />} className="text-meta text-ink-subtle">
            {description}
          </Box>
        </Stack>
      </Inline>
      <Inline gap="sm" align="center" className="shrink-0">
        {action}
      </Inline>
    </Inline>
  )
}

function DisconnectAction({
  service,
  consequence,
  onConfirm,
}: {
  service: string
  consequence: string
  onConfirm: () => void
}) {
  return (
    <ConfirmableAction
      trigger={
        <Button variant="outline" size="compact">
          Disconnect
        </Button>
      }
      title={`Disconnect ${service}?`}
      description={consequence}
      confirmLabel={`Disconnect ${service}`}
      confirmIcon={LogOut}
      // The row flips optimistically and rolls back with a toast on failure.
      onConfirm={async () => onConfirm()}
    />
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
    <ConnectionRow
      provider="slack"
      name="Slack"
      connected={connected}
      description={
        connected
          ? `Linked to Slack member ${slackUserId}${workEmail ? ` · ${workEmail}` : ""}.`
          : "Sign in with Slack so Open SWE resolves your GitHub account when you tag it — the verified email also resolves Linear mentions."
      }
      action={
        user.slack_oauth_enabled ? (
          <Button
            size="compact"
            variant={connected ? "outline" : "primary"}
            onClick={connect}
            disabled={connecting}
          >
            {connecting ? "Redirecting…" : connected ? "Reconnect" : "Connect"}
          </Button>
        ) : (
          <Box render={<span />} className="text-meta text-ink-subtle">
            Sign in with Slack unavailable
          </Box>
        )
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
    <ConnectionRow
      provider="notion"
      name="Notion"
      connected={connected}
      description="Let agent runs use Notion MCP tools with your workspace permissions. OAuth tokens are encrypted at rest and scoped to your account."
      action={
        connected ? (
          <DisconnectAction
            service="Notion"
            consequence="Agent runs lose access to Notion MCP tools until you connect again. Nothing in your Notion workspace changes."
            onConfirm={() => disconnect.mutate()}
          />
        ) : (
          <Button
            size="compact"
            onClick={connect}
            disabled={connecting || creds.isLoading}
          >
            {connecting ? "Redirecting…" : "Connect"}
          </Button>
        )
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
  const status = useLangSmithConnection()
  const [connecting, setConnecting] = useState(false)
  return (
    <Button
      size={size === "sm" ? "compact" : "control"}
      onClick={() => {
        setConnecting(true)
        connectLangSmith(window.location.href)
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
    <ConnectionRow
      provider="langsmith"
      name="LangSmith"
      connected={connected}
      description={
        connected
          ? `Signed in${status.data.email ? ` as ${status.data.email}` : ""}. Open SWE can call LangSmith as you in your private threads.`
          : "Sign in with LangSmith so Open SWE can call LangSmith as you in your private threads."
      }
      action={
        connected ? (
          <DisconnectAction
            service="LangSmith"
            consequence="Open SWE stops calling LangSmith as you in your private threads until you sign in again."
            onConfirm={() => disconnect.mutate()}
          />
        ) : (
          <ConnectLangSmithButton />
        )
      }
    />
  )
}

export function ConnectionsSection({ user }: { user: SessionUser }) {
  return (
    <PageSection title="Accounts" contained>
      <Stack gap="none">
        <SlackRow user={user} />
        <NotionRow />
        <LangSmithRow />
      </Stack>
    </PageSection>
  )
}
