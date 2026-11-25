import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Logo } from "@langchain/macaw-components/Logo"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

import type { SessionUser, SlackRecovery } from "@/lib/api"
import {
  CONNECT_BADGE_BUTTON,
  ConnectBadge,
  ConnectionRow,
  ConnectionStatusBadge,
} from "./ConnectionsTable"
import { ConnectorLogo } from "@/components/ConnectorLogo"
import { connectService, loginUrl } from "@/lib/api"

function GitHubBrandLogo() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 256 250"
      fill="currentColor"
      className="size-4 text-primary"
    >
      <path d="M128.001 0C57.317 0 0 57.307 0 128.001c0 56.554 36.676 104.535 87.535 121.46 6.397 1.185 8.746-2.777 8.746-6.158 0-3.052-.12-13.135-.174-23.83-35.61 7.742-43.124-15.103-43.124-15.103-5.823-14.795-14.213-18.73-14.213-18.73-11.613-7.944.876-7.78.876-7.78 12.853.902 19.621 13.19 19.621 13.19 11.417 19.568 29.945 13.911 37.249 10.64 1.149-8.272 4.466-13.92 8.127-17.116-28.431-3.236-58.318-14.212-58.318-63.258 0-13.975 5-25.394 13.188-34.358-1.329-3.224-5.71-16.242 1.24-33.874 0 0 10.749-3.44 35.21 13.121 10.21-2.836 21.16-4.258 32.038-4.307 10.878.049 21.837 1.47 32.066 4.307 24.431-16.56 35.165-13.12 35.165-13.12 6.967 17.63 2.584 30.65 1.255 33.873 8.207 8.964 13.173 20.383 13.173 34.358 0 49.163-29.944 59.988-58.447 63.157 4.591 3.972 8.682 11.762 8.682 23.704 0 17.126-.148 30.91-.148 35.126 0 3.407 2.304 7.398 8.792 6.14C219.37 232.5 256 184.537 256 128.002 256 57.307 198.691 0 128.001 0Z" />
    </svg>
  )
}

function SlackBrandLogo() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 127 127"
      fill="none"
      className="size-4"
    >
      <path
        d="M27.2 80c0 7.3-5.9 13.2-13.2 13.2C6.7 93.2.8 87.3.8 80c0-7.3 5.9-13.2 13.2-13.2h13.2V80zm6.6 0c0-7.3 5.9-13.2 13.2-13.2 7.3 0 13.2 5.9 13.2 13.2v33c0 7.3-5.9 13.2-13.2 13.2-7.3 0-13.2-5.9-13.2-13.2V80z"
        fill="#E01E5A"
      />
      <path
        d="M47 27c-7.3 0-13.2-5.9-13.2-13.2C33.8 6.5 39.7.6 47 .6c7.3 0 13.2 5.9 13.2 13.2V27H47zm0 6.7c7.3 0 13.2 5.9 13.2 13.2 0 7.3-5.9 13.2-13.2 13.2H13.9C6.6 60.1.7 54.2.7 46.9c0-7.3 5.9-13.2 13.2-13.2H47z"
        fill="#36C5F0"
      />
      <path
        d="M99.9 46.9c0-7.3 5.9-13.2 13.2-13.2 7.3 0 13.2 5.9 13.2 13.2 0 7.3-5.9 13.2-13.2 13.2H99.9V46.9zm-6.6 0c0 7.3-5.9 13.2-13.2 13.2-7.3 0-13.2-5.9-13.2-13.2V13.8C66.9 6.5 72.8.6 80.1.6c7.3 0 13.2 5.9 13.2 13.2v33.1z"
        fill="#2EB67D"
      />
      <path
        d="M80.1 99.8c7.3 0 13.2 5.9 13.2 13.2 0 7.3-5.9 13.2-13.2 13.2-7.3 0-13.2-5.9-13.2-13.2V99.8h13.2zm0-6.6c-7.3 0-13.2-5.9-13.2-13.2 0-7.3 5.9-13.2 13.2-13.2h33.1c7.3 0 13.2 5.9 13.2 13.2 0 7.3-5.9 13.2-13.2 13.2H80.1z"
        fill="#ECB22E"
      />
    </svg>
  )
}

export function readSlackRecoveryFromUrl(): SlackRecovery | null {
  if (typeof window === "undefined") return null
  const params = new URLSearchParams(window.location.search)
  const recoveryToken = params.get("slack_recovery")
  if (!recoveryToken) return null
  const error = params.get("recovery_error")
  const knownError = [
    "Slack recovery expired or changed — connect Slack again",
    "This is not the GitHub account connected to your Slack. Try again with the GitHub account you previously used with Robin.",
    "This GitHub account is already linked to another LangSmith account. Sign in with that LangSmith account to keep using your existing Robin account.",
    "Your account changed — sign in again before linking GitHub",
  ].find((message) => message === error)
  return {
    recoveryToken,
    error:
      knownError ??
      (error ? "Could not recover your account. Please try again." : undefined),
  }
}

/** The status column of a core connection that isn't connected yet. */
function CoreConnectionStatus({
  connected,
  action,
  label = "Connect",
  connecting,
  onConnect,
}: {
  connected: boolean
  /** Names the connect action, such as "Connect GitHub"; absent when it can't be connected here. */
  action?: string
  label?: string
  connecting: boolean
  onConnect: () => void
}) {
  if (connected || !action)
    return <ConnectionStatusBadge connected={connected} />
  return (
    <Button
      variant="plain"
      color="secondary"
      size="xs"
      className={CONNECT_BADGE_BUTTON}
      aria-label={action}
      disabled={connecting}
      onClick={onConnect}
    >
      <ConnectBadge>{connecting ? "Redirecting…" : label}</ConnectBadge>
    </Button>
  )
}

export function SlackRow({
  user,
  onRecovery,
}: {
  user: SessionUser
  onRecovery: (recovery: SlackRecovery) => void
}) {
  const qc = useQueryClient()
  const [connecting, setConnecting] = useState(false)

  const slackUserId = user.slack_user_id ?? null
  const connected = !!slackUserId
  const enabled = !!user.slack_oauth_enabled

  const connect = () => {
    setConnecting(true)
    // The link lands on the session's user row; refresh it when the OAuth redirect returns.
    void qc.invalidateQueries({ queryKey: ["session"] })
    void connectService("slack")
      ?.then((result) => {
        if (typeof result === "object") onRecovery(result)
      })
      .finally(() => {
        setConnecting(false)
        void qc.invalidateQueries({ queryKey: ["session"] })
      })
  }

  return (
    <ConnectionRow
      name="Slack"
      logo={<ConnectorLogo name="Slack" icon={<SlackBrandLogo />} />}
      detail={
        connected
          ? slackUserId
          : enabled
            ? "Lets Robin know you in Slack"
            : "Sign in with Slack unavailable"
      }
      detailTitle={
        connected
          ? `Connected to Slack member ${slackUserId}`
          : enabled
            ? "Connect Slack so Robin recognizes you when you tag it. Your verified email also connects Linear mentions."
            : undefined
      }
      showDetailInName
      status={
        <CoreConnectionStatus
          connected={connected}
          action={enabled ? "Connect Slack" : undefined}
          connecting={connecting}
          onConnect={connect}
        />
      }
      actions={
        connected && enabled
          ? [{ label: "Reconnect", onSelect: connect, disabled: connecting }]
          : []
      }
    />
  )
}

/** Sends the person through GitHub to connect (or reconnect) the identity coding features use. */
function useConnectGitHub() {
  const qc = useQueryClient()
  const [connecting, setConnecting] = useState(false)
  return {
    connecting,
    connect: () => {
      setConnecting(true)
      void connectService("github", window.location.href)?.finally(() => {
        setConnecting(false)
        void qc.invalidateQueries({ queryKey: ["session"] })
      })
    },
  }
}

/** Connect (or reconnect) the signed-in person's GitHub connection, which unlocks coding features. */
export function LinkGitHubButton({
  user,
  size = "xs",
  className,
}: {
  user: SessionUser
  size?: "xs" | "sm" | "md"
  className?: string
}) {
  const github = useConnectGitHub()
  const linked = !!user.github_login
  return (
    <Button
      size={size}
      color={linked ? "secondary" : "primary"}
      variant={linked ? "outlined" : "normal"}
      leftDecorator={GithubLogoIcon}
      className={className}
      onClick={github.connect}
      disabled={github.connecting}
    >
      {github.connecting
        ? "Redirecting…"
        : linked
          ? "Reconnect"
          : "Connect GitHub"}
    </Button>
  )
}

export function GitHubRow({ user }: { user: SessionUser }) {
  const github = useConnectGitHub()
  const linked = !!user.github_login
  const linkable = user.auth_provider === "langsmith"
  return (
    <ConnectionRow
      name="GitHub"
      logo={<ConnectorLogo name="GitHub" icon={<GitHubBrandLogo />} />}
      detail={linked ? `@${user.github_login}` : "Needed for coding features"}
      detailTitle={
        linked
          ? `Connected as @${user.github_login}. Coding features act as this GitHub user.`
          : "Connect GitHub to use coding features and return to your existing coding threads."
      }
      showDetailInName
      status={
        <CoreConnectionStatus
          connected={linked}
          action={linkable ? "Connect GitHub" : undefined}
          connecting={github.connecting}
          onConnect={github.connect}
        />
      }
      actions={
        linked && linkable
          ? [
              {
                label: "Reconnect",
                onSelect: github.connect,
                disabled: github.connecting,
              },
            ]
          : []
      }
    />
  )
}

/** The LangSmith sign-in whose grant Robin uses; `expired` when LangSmith no longer accepts it. */
export function LangSmithRow({
  user,
  expired,
}: {
  user: SessionUser
  expired: boolean
}) {
  const [connecting, setConnecting] = useState(false)
  return (
    <ConnectionRow
      name="LangSmith"
      logo={
        <ConnectorLogo
          name="LangSmith"
          icon={
            <Logo
              brand="langsmith"
              variant="logomark"
              size="sm"
              className="size-4"
            />
          }
        />
      }
      detail={expired ? "Sign-in expired" : (user.email ?? "Signed in")}
      detailTitle={
        expired
          ? "Sign in to LangSmith again so Robin can use your connections."
          : "Robin signs you in and uses your tools through LangSmith."
      }
      showDetailInName
      status={
        <CoreConnectionStatus
          connected={!expired}
          action="Sign in to LangSmith again"
          label="Sign in"
          connecting={connecting}
          onConnect={() => {
            setConnecting(true)
            window.location.assign(loginUrl())
          }}
        />
      }
      actions={[]}
    />
  )
}

/** Sends the person back through LangSmith sign-in, which renews the grant their connections use. */
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
