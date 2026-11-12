import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type { SessionUser, SlackRecovery } from "@/lib/api"
import { SlackRecoveryDialog } from "./SlackRecoveryDialog"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { connectService, loginUrl } from "@/lib/api"

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
  const [recovery, setRecovery] = useState<SlackRecovery | null>(() => {
    if (typeof window === "undefined") return null
    const params = new URLSearchParams(window.location.search)
    const recoveryToken = params.get("slack_recovery")
    const error = params.get("recovery_error")
    const knownError = [
      "Slack recovery expired or changed — connect Slack again",
      "This is not the GitHub account connected to your Slack. Try again with the GitHub account you previously used with Robin.",
      "This GitHub account is already linked to another LangSmith account. Sign in with that LangSmith account to keep using your existing Robin account.",
      "Your account changed — sign in again before linking GitHub",
    ].find((message) => message === error)
    return recoveryToken
      ? {
          recoveryToken,
          error:
            knownError ??
            (error
              ? "Could not recover your account. Please try again."
              : undefined),
        }
      : null
  })

  const slackUserId = user.slack_user_id ?? null
  const workEmail = user.email ?? null
  const connected = !!slackUserId

  const connect = () => {
    setConnecting(true)
    // The link lands on the session's user row; refresh it when the OAuth redirect returns.
    void qc.invalidateQueries({ queryKey: ["session"] })
    void connectService("slack")
      ?.then((result) => {
        if (typeof result === "object") setRecovery(result)
      })
      .finally(() => {
        setConnecting(false)
        void qc.invalidateQueries({ queryKey: ["session"] })
      })
  }

  return (
    <>
      {recovery && (
        <SlackRecoveryDialog
          recovery={recovery}
          onCancel={() => setRecovery(null)}
        />
      )}
      <SettingsRow
        label="Slack"
        description={
          connected
            ? `Linked to Slack member ${slackUserId}${workEmail ? ` · ${workEmail}` : ""}.`
            : "Connect Slack so Robin recognizes you when you tag it. Your verified email also connects Linear mentions."
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
    </>
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
      description={`Your sign-in account${user.email ? `: ${user.email}` : ""}. Robin calls LangSmith as you with it. Sign out to change accounts.`}
      control={<StatusPill connected />}
    />
  )
}

export function ConnectionsSection({ user }: { user: SessionUser }) {
  return (
    <SettingsSection title="Accounts">
      <GitHubRow user={user} />
      <SlackRow user={user} />
      <LangSmithRow user={user} />
    </SettingsSection>
  )
}
