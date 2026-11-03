import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type { SessionUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { connectService, loginUrl } from "@/lib/api"
import { hasLinkedGitHub } from "@/lib/linkedGitHub"

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

/** Link (or relink) the signed-in person's GitHub account, which unlocks coding features. */
export function LinkGitHubButton({
  user,
  size = "xs",
  className,
}: {
  user: SessionUser
  size?: "xs" | "sm" | "md"
  className?: string
}) {
  const qc = useQueryClient()
  const [connecting, setConnecting] = useState(false)
  const linked = !!user.github_login
  return (
    <Button
      size={size}
      color={linked ? "secondary" : "primary"}
      variant={linked ? "outlined" : "normal"}
      leftDecorator={GithubLogoIcon}
      className={className}
      onClick={() => {
        setConnecting(true)
        void connectService("github", window.location.href)?.finally(() => {
          setConnecting(false)
          void qc.invalidateQueries({ queryKey: ["session"] })
        })
      }}
      disabled={connecting}
    >
      {connecting ? "Redirecting…" : linked ? "Relink" : "Link GitHub"}
    </Button>
  )
}

function GitHubRow({ user }: { user: SessionUser }) {
  const linked = !!user.github_login
  return (
    <SettingsRow
      label="GitHub"
      description={
        linked
          ? `Linked to @${user.github_login}. Coding features act as this account.`
          : "Link your GitHub account to use coding features and return to your existing coding threads."
      }
      control={
        <div className="flex items-center gap-space-2">
          <StatusPill connected={linked} />
          {user.auth_provider === "langsmith" && (
            <LinkGitHubButton user={user} />
          )}
        </div>
      }
    />
  )
}

/** Sends the person back through LangSmith sign-in, which renews the grant their tools use. */
export function SignInWithLangSmithButton({
  size = "xs",
}: {
  size?: "xs" | "sm" | "md"
}) {
  return (
    <Button as={<a href={loginUrl()} />} size={size} color="primary">
      Sign in again
    </Button>
  )
}

function LangSmithRow({ user }: { user: SessionUser }) {
  if (user.auth_provider !== "langsmith") return null
  return (
    <SettingsRow
      label="LangSmith"
      description={`Your sign-in account${user.email ? `: ${user.email}` : ""}. Open SWE calls LangSmith as you with it. Sign out to change accounts.`}
      control={<StatusPill connected />}
    />
  )
}

export function ConnectionsSection({ user }: { user: SessionUser }) {
  return (
    <SettingsSection title="Accounts">
      <GitHubRow user={user} />
      {hasLinkedGitHub(user) && <SlackRow user={user} />}
      <LangSmithRow user={user} />
    </SettingsSection>
  )
}
