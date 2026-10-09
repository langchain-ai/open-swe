import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Select } from "@langchain/macaw-components/Select"
import { Switch } from "@langchain/macaw-components/Switch"

import type { Theme } from "@/lib/theme"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import {
  notificationsEnabled,
  notificationsSupported,
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"
import { agentsApi } from "@/features/agents/lib/api"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { useSidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import { api } from "@/lib/api"
import type {
  FollowUpBehavior,
  ThreadVisibility,
  UserPreferences,
} from "@/lib/api"
import { useTheme } from "@/lib/theme"
import { ConfirmDialog } from "./ConfirmDialog"

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
  const { prefs, setCollapseSubagentsByDefault } = useSidebarPrefs()
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
          description="Color theme for the web app on this device."
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
          description="Queue follow-ups until the run ends, or steer the current run with them. ⌘↵ does the opposite for one message; Enter on an empty composer sends the next queued message now."
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
          label="Collapse subagent threads"
          description="Keep nested subagent threads folded in the sidebar until you expand them."
          control={
            <Switch
              aria-label="Collapse subagent threads"
              checked={prefs.collapseSubagentsByDefault}
              onChange={setCollapseSubagentsByDefault}
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
                : "Notify me when an agent run finishes."
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

      {typeof window !== "undefined" && window.openSweDesktop && (
        <SettingsSection title="Desktop app">
          <SettingsRow
            label="Local tracing project"
            description="Project used for local desktop runs. Leave blank to use the shared cloud project. Restart the desktop app after changing it."
            control={
              <LocalTracingProjectInput
                // Remount once preferences load so the field shows the saved value.
                key={`${preferences.isSuccess}`}
                saved={preferences.data?.local_tracing_project ?? ""}
                placeholder={
                  preferences.data?.default_local_tracing_project ??
                  "Shared cloud project"
                }
                disabled={preferences.isLoading}
                onSave={(value) =>
                  savePreferences.mutate({ local_tracing_project: value })
                }
              />
            }
          />
        </SettingsSection>
      )}

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

function LocalTracingProjectInput({
  saved,
  placeholder,
  disabled,
  onSave,
}: {
  saved: string
  placeholder: string
  disabled: boolean
  onSave: (value: string | null) => void
}) {
  const [draft, setDraft] = useState(saved)
  return (
    <Input
      aria-label="Local tracing project"
      size="md"
      className="w-56"
      placeholder={placeholder}
      value={draft}
      onChange={setDraft}
      disabled={disabled}
      onBlur={() => onSave(draft.trim() || null)}
    />
  )
}
