import { Navigate, createFileRoute, useNavigate } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { IoLogoSlack } from "react-icons/io5"
import { SiNotion } from "react-icons/si"

import type { CurrentsConnectBody, SessionUser } from "@/lib/api"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { ProviderTokensSection } from "@/components/ProviderTokensSection"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { api, notionConnectUrl, slackConnectUrl } from "@/lib/api"
import {
  buildProfileUpdate,
  useOptions,
  useProfile,
  useSaveProfile,
} from "@/lib/profile"
import { useSession } from "@/lib/session"
import {
  notificationsEnabled,
  notificationsSupported,
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/my-settings")({
  component: MySettingsPage,
})

type DraftReviewChoice = "team_default" | "always_on" | "always_off"

function toChoice(value: boolean | null | undefined): DraftReviewChoice {
  if (value === true) return "always_on"
  if (value === false) return "always_off"
  return "team_default"
}

function fromChoice(choice: DraftReviewChoice): boolean | null {
  if (choice === "always_on") return true
  if (choice === "always_off") return false
  return null
}

function UserMappingSection({ session }: { session: SessionUser }) {
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
    window.location.assign(slackConnectUrl())
  }

  return (
    <SettingsSection
      title="User mapping"
      description="Connect your Slack account so Open SWE can resolve your GitHub account when you tag it in Slack. We use the email Slack verifies, which also lets Linear mentions resolve to you."
    >
      <div className="divide-y divide-border">
        <SettingsRow
          label="GitHub account"
          control={
            <span className="text-xs text-muted-foreground">
              {session.login}
            </span>
          }
        />
        <SettingsRow
          label="Slack account"
          description={
            connected
              ? `Linked to Slack member ${slackUserId}${workEmail ? ` · ${workEmail}` : ""}.`
              : "Not connected. Sign in with Slack to verify your identity — no manual IDs to copy."
          }
          control={
            <div className="flex items-center gap-2">
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
              {session.slack_oauth_enabled ? (
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
                      : "Connect Slack"}
                </Button>
              ) : (
                <span className="text-[10px] text-muted-foreground">
                  Sign in with Slack unavailable
                </span>
              )}
            </div>
          }
        />
      </div>
    </SettingsSection>
  )
}

function NotificationsSection() {
  const supported = notificationsSupported()
  const [enabled, setEnabled] = useState(() => notificationsEnabled())
  const [permissionDenied, setPermissionDenied] = useState(
    () => supported && Notification.permission === "denied"
  )

  const handleToggle = async (checked: boolean) => {
    if (checked) {
      const perm = await requestNotificationPermission()
      if (perm === "granted") {
        setNotificationsPref(true)
        setEnabled(true)
      } else if (perm === "denied") {
        setPermissionDenied(true)
      }
    } else {
      setNotificationsPref(false)
      setEnabled(false)
    }
  }

  if (!supported) {
    return (
      <SettingsSection title="Notifications">
        <SettingsRow
          label="Desktop notifications"
          description="Your browser does not support desktop notifications."
          control={
            <span className="text-[10px] text-muted-foreground">
              Not supported
            </span>
          }
        />
      </SettingsSection>
    )
  }

  return (
    <SettingsSection
      title="Notifications"
      description="Get a desktop notification when an agent run finishes."
    >
      <SettingsRow
        label="Desktop notifications"
        description={
          permissionDenied
            ? "Permission was denied. Re-enable it in your browser's site settings."
            : "Show a notification when a run completes."
        }
        control={
          <Switch
            checked={enabled}
            onCheckedChange={handleToggle}
            disabled={permissionDenied}
          />
        }
      />
    </SettingsSection>
  )
}

function CurrentsCredentialsSection() {
  const qc = useQueryClient()
  const creds = useQuery({
    queryKey: ["myCurrents"],
    queryFn: api.getMyCurrentsStatus,
  })
  const [error, setError] = useState<string | null>(null)
  const [apiKey, setApiKey] = useState("")

  const onSuccess = () => {
    qc.invalidateQueries({ queryKey: ["myCurrents"] })
    setError(null)
    setApiKey("")
  }
  const onError = (e: Error) => setError(e.message)

  const connect = useMutation({
    mutationFn: (body: CurrentsConnectBody) => api.connectCurrents(body),
    onSuccess,
    onError,
  })
  const disconnect = useMutation({
    mutationFn: () => api.disconnectCurrents(),
    onSuccess,
    onError,
  })

  const connected = creds.data?.connected

  return (
    <SettingsSection
      title="Currents.dev"
      description="Connect your personal Currents API key to let agent runs inspect e2e test results — failed specs, error traces, screenshots, and DOM snapshots. The key is encrypted at rest and scoped to your account only."
    >
      <SettingsRow
        label="API key"
        description={
          connected
            ? `Connected · key ••••${creds.data?.api_key_last4 ?? ""}`
            : "Find your API key in Currents under Organization → API & Record Keys."
        }
        control={
          connected ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              Disconnect
            </Button>
          ) : (
            <div className="flex flex-col items-end gap-2">
              <Input
                className="w-56"
                placeholder="Currents API key"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                disabled={creds.isLoading}
              />
              <Button
                size="sm"
                onClick={() => connect.mutate({ api_key: apiKey.trim() })}
                disabled={connect.isPending || !apiKey.trim()}
              >
                Connect
              </Button>
            </div>
          )
        }
      />
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}

function NotionCredentialsSection() {
  const qc = useQueryClient()
  const creds = useQuery({
    queryKey: ["myNotion"],
    queryFn: api.getMyNotionStatus,
  })
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSuccess = () => {
    qc.invalidateQueries({ queryKey: ["myNotion"] })
    setError(null)
  }
  const onError = (e: Error) => setError(e.message)

  const disconnect = useMutation({
    mutationFn: () => api.disconnectNotion(),
    onSuccess,
    onError,
  })

  const connected = creds.data?.connected
  const connect = () => {
    setConnecting(true)
    void qc.invalidateQueries({ queryKey: ["myNotion"] })
    window.location.assign(notionConnectUrl())
  }

  return (
    <SettingsSection
      title="Notion"
      description="Connect your Notion account through Notion MCP so agent runs can use Notion tools with your workspace permissions. OAuth tokens are encrypted at rest and scoped to your account only."
    >
      <SettingsRow
        label="Notion MCP"
        description={
          connected
            ? "Connected. Reconnect if your Notion authorization expires or workspace access changes."
            : "Not connected. Sign in with Notion to authorize the hosted Notion MCP server."
        }
        control={
          <div className="flex items-center gap-2">
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
                {connecting ? "Redirecting…" : "Connect Notion"}
              </Button>
            )}
          </div>
        }
      />
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}

function MySettingsPage() {
  const session = useSession()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const profile = useProfile()
  const options = useOptions()
  const save = useSaveProfile()
  const teamSettings = useQuery({
    queryKey: ["teamSettings"],
    queryFn: api.getTeamSettings,
    enabled: !!session.data,
  })
  const [error, setError] = useState<string | null>(null)

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  const handleLogout = async () => {
    await api.logout()
    qc.setQueryData(["session"], null)
    void navigate({ to: "/login" })
  }

  const firstModel = options.data?.models[0]
  const fallbackModel =
    options.data?.default_agent_model ?? firstModel?.id ?? ""
  const fallbackEffort =
    options.data?.default_agent_reasoning_effort ??
    firstModel?.default_effort ??
    ""

  const draftChoice = toChoice(profile.data?.review_draft_prs)
  const teamDefaultOn = teamSettings.data?.review_draft_prs ?? false
  const teamDefaultLabel = `Use team default (currently: ${teamDefaultOn ? "On" : "Off"})`

  const handleDraftChoiceChange = (next: DraftReviewChoice) => {
    setError(null)
    save
      .mutateAsync(
        buildProfileUpdate(
          profile.data,
          { review_draft_prs: fromChoice(next) },
          fallbackModel,
          fallbackEffort
        )
      )
      .catch((e: Error) => setError(e.message))
  }

  return (
    <AppShell user={session.data} title="Profile Settings">
      <SettingsSection title="Profile">
        <SettingsRow
          label="Email"
          control={
            <span className="text-xs text-muted-foreground">
              {session.data.email ?? "—"}
            </span>
          }
        />
      </SettingsSection>

      <UserMappingSection session={session.data} />

      <SettingsSection title="Open SWE Review">
        <SettingsRow
          label="Review my draft PRs"
          description="Whether Open SWE Review runs on pull requests you open in draft. When set to the team default, your admin's org-wide setting applies."
          control={
            <Select
              value={draftChoice}
              onValueChange={(v) =>
                handleDraftChoiceChange(v as DraftReviewChoice)
              }
              disabled={profile.isLoading || save.isPending}
            >
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="team_default">{teamDefaultLabel}</SelectItem>
                <SelectItem value="always_on">
                  Always review my drafts
                </SelectItem>
                <SelectItem value="always_off">
                  Never review my drafts
                </SelectItem>
              </SelectContent>
            </Select>
          }
        />
      </SettingsSection>

      <NotificationsSection />

      <CurrentsCredentialsSection />

      <NotionCredentialsSection />

      <ProviderTokensSection />

      <SettingsSection title="Account">
        <SettingsRow
          label="Sign out"
          description="End your dashboard session."
          control={
            <Button
              size="sm"
              variant="outline"
              onClick={() => void handleLogout()}
            >
              Sign out
            </Button>
          }
        />
      </SettingsSection>

      {error && <p className="text-xs text-destructive">{error}</p>}
    </AppShell>
  )
}
