import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Select } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"

import type { Theme } from "@/lib/theme"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import { useSession } from "@/lib/session"
import { useProfileSettings } from "./ProfileSwitchRow"
import {
  notificationsEnabled,
  notificationsSupported,
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"
import { agentsApi } from "@/features/agents/lib/api"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { api } from "@/lib/api"
import type {
  FollowUpBehavior,
  ThreadVisibility,
  UserPreferences,
} from "@/lib/api"
import { sessionQueryOptions } from "@/lib/session"
import { useTheme } from "@/lib/theme"
import { ConfirmDialog } from "@/components/ConfirmDialog"

const THEMES: Array<{ value: Theme; label: string }> = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
]

const VISIBILITIES: Array<{ value: ThreadVisibility; label: string }> = [
  { value: "private", label: "Private · only me" },
  { value: "public", label: "Workspace" },
]

const FOLLOW_UP_BEHAVIORS: Array<{ value: FollowUpBehavior; label: string }> = [
  { value: "queue", label: "Queue" },
  { value: "steer", label: "Steer" },
]

// Select values are non-empty strings, so "no default" needs a sentinel that
// is translated back to null on save.
const NO_DEFAULT_WORKSPACE = "__no_default_workspace__"

const PREFERENCES_KEY = ["myPreferences"]
const SAVE_PREFERENCES_KEY = ["saveMyPreferences"]

export function GeneralSettings() {
  const { theme, setTheme } = useTheme()
  const { profile, ready, save } = useProfileSettings()
  const session = useSession()
  const qc = useQueryClient()
  const pendingPreferences = useMutationState({
    filters: {
      mutationKey: SAVE_PREFERENCES_KEY,
      exact: true,
      status: "pending",
    },
    select: (m) => m.state.variables as Partial<UserPreferences>,
  })
  const preferences = useQuery({
    queryKey: PREFERENCES_KEY,
    queryFn: api.getMyPreferences,
    select: (saved) =>
      pendingPreferences.reduce<UserPreferences>(
        (merged, patch) => ({ ...merged, ...patch }),
        saved
      ),
  })
  const savePreferences = useMutation({
    mutationKey: SAVE_PREFERENCES_KEY,
    scope: { id: SAVE_PREFERENCES_KEY.join(":") },
    meta: { errorTitle: "Couldn't save preferences" },
    mutationFn: (patch: Partial<UserPreferences>) => {
      const saved = qc.getQueryData<UserPreferences>(PREFERENCES_KEY)
      if (!saved) throw new Error("Preferences are not loaded.")
      return api.saveMyPreferences({ ...saved, ...patch })
    },
    onMutate: () => qc.cancelQueries({ queryKey: PREFERENCES_KEY }),
    onSuccess: (data) => {
      qc.setQueryData(PREFERENCES_KEY, data)
      void qc.invalidateQueries({ queryKey: sessionQueryOptions.queryKey })
    },
    onSettled: () =>
      qc.isMutating({ mutationKey: SAVE_PREFERENCES_KEY }) > 1
        ? undefined
        : qc.invalidateQueries({ queryKey: PREFERENCES_KEY }),
  })
  const workspaceOptions = useWorkspaceOptions()
  const workspaceItems = [
    { value: NO_DEFAULT_WORKSPACE, label: "Workspace default" },
    ...(workspaceOptions.data?.workspaces ?? []).map((workspace) => ({
      value: workspace.slug,
      label: workspace.name,
    })),
  ]
  const [confirmingArchive, setConfirmingArchive] = useState(false)
  const archiveThreads = useMutation({
    meta: { errorTitle: "Couldn't archive threads" },
    mutationFn: agentsApi.resolveAllThreads,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-threads"] }),
    onSettled: () => setConfirmingArchive(false),
  })
  const supported = notificationsSupported()
  const [enabled, setEnabled] = useState(() => notificationsEnabled())
  const [denied, setDenied] = useState(
    () => supported && Notification.permission === "denied"
  )

  const toggleNotifications = async (checked: boolean) => {
    if (!checked) {
      setNotificationsPref(false)
      setEnabled(false)
      return
    }
    const permission = await requestNotificationPermission()
    if (permission === "granted") {
      setNotificationsPref(true)
      setEnabled(true)
    } else if (permission === "denied") {
      setDenied(true)
    }
  }

  return (
    <>
      <SettingsSection title="Appearance">
        <SettingsRow
          label="Theme"
          description="Color theme for the dashboard on this device."
          control={
            <Select
              aria-label="Theme"
              options={THEMES}
              value={theme}
              onChange={(v) => v && setTheme(v)}
              triggerClassName="w-40"
            />
          }
        />
      </SettingsSection>

      <SettingsSection
        title="Threads"
        description="Defaults for new threads and how you interact with running ones."
      >
        <SettingsRow
          label="Default visibility"
          description="Private threads can use your personal connections and only you can prompt them. Workspace threads are open to everyone. Visibility can't change later."
          control={
            <Select
              aria-label="Default visibility"
              options={VISIBILITIES}
              value={preferences.data?.default_visibility ?? "private"}
              onChange={(v) =>
                v && savePreferences.mutate({ default_visibility: v })
              }
              disabled={preferences.isLoading}
              triggerClassName="w-40"
            />
          }
        />
        <SettingsRow
          label="Default workspace"
          description="Preselected in the composer when the chosen repository doesn't belong to another workspace."
          control={
            <Select
              aria-label="Default workspace"
              options={workspaceItems}
              value={
                preferences.data?.default_workspace ?? NO_DEFAULT_WORKSPACE
              }
              onChange={(v) =>
                v &&
                savePreferences.mutate({
                  default_workspace: v === NO_DEFAULT_WORKSPACE ? null : v,
                })
              }
              disabled={preferences.isLoading || workspaceOptions.isLoading}
              triggerClassName="w-48"
            />
          }
        />
        <SettingsRow
          label="Follow-up behavior"
          description="Queue follow-ups until the run ends, or steer the current run with them. ⌘↵ does the opposite for one message."
          control={
            <Select
              aria-label="Follow-up behavior"
              options={FOLLOW_UP_BEHAVIORS}
              value={preferences.data?.follow_up_behavior ?? "steer"}
              onChange={(v) =>
                v && savePreferences.mutate({ follow_up_behavior: v })
              }
              disabled={preferences.isLoading}
              triggerClassName="w-40"
            />
          }
        />
        <SettingsRow
          label="Show changes when a run finishes"
          description="Open the Changes panel when a run that edited files finishes."
          control={
            <Switch
              aria-label="Show changes when a run finishes"
              checked={preferences.data?.auto_show_changes ?? true}
              onChange={(v) => savePreferences.mutate({ auto_show_changes: v })}
              disabled={preferences.isLoading}
            />
          }
        />
      </SettingsSection>

      <SettingsSection title="Slack">
        <SettingsRow
          label="Personal kitchen channel"
          description={
            session.data?.slack_user_id
              ? "Open SWE responds to your messages in this channel without an @mention. Only your messages get kitchen handling; workspace kitchen channels stay unchanged. Choose an internal channel Open SWE has joined. Clear to turn off."
              : "Link Slack in Connections to choose a channel where Open SWE responds to your messages without an @mention."
          }
          control={
            <SlackChannelCombobox
              aria-label="Personal kitchen channel"
              placeholder="No personal kitchen"
              value={profile?.personal_kitchen_channel_id || null}
              disabled={!ready || !session.data?.slack_user_id}
              eligible={(channel) =>
                channel.is_member &&
                !channel.is_ext_shared &&
                !channel.is_pending_ext_shared
              }
              onValueChange={(value) =>
                save({ personal_kitchen_channel_id: value ?? "" })
              }
              className="w-56 max-w-full"
            />
          }
        />
      </SettingsSection>

      <SettingsSection title="Notifications">
        <SettingsRow
          label="Desktop notifications"
          description={
            !supported
              ? "Your browser doesn't support desktop notifications."
              : denied
                ? "Permission was denied. Re-enable it in your browser's site settings."
                : "Notify me when an agent run finishes or someone mentions me. Mentions I miss here arrive as a Slack DM."
          }
          control={
            <Switch
              aria-label="Desktop notifications"
              checked={enabled}
              onChange={(v) => void toggleNotifications(v)}
              disabled={!supported || denied}
            />
          }
        />
      </SettingsSection>

      <SettingsSection title="Data">
        <SettingsRow
          label="Archive all threads"
          description={
            archiveThreads.isSuccess
              ? `${archiveThreads.data.resolved} threads archived.`
              : "Resolve every thread you've participated in. They stay available in the resolved view."
          }
          control={
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              disabled={archiveThreads.isPending}
              onClick={() => setConfirmingArchive(true)}
            >
              {archiveThreads.isPending ? "Archiving…" : "Archive all"}
            </Button>
          }
        />
      </SettingsSection>
      <ConfirmDialog
        open={confirmingArchive}
        onOpenChange={setConfirmingArchive}
        title="Archive all your threads?"
        description="They will remain available in the resolved view."
        confirmLabel="Archive all"
        pendingLabel="Archiving…"
        pending={archiveThreads.isPending}
        onConfirm={() => archiveThreads.mutate()}
      />
    </>
  )
}
