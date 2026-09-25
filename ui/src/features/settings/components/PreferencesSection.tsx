import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"

import type { Theme } from "@/lib/theme"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import {
  notificationsEnabled,
  notificationsSupported,
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"
import { agentsApi } from "@/features/agents/lib/api"
import { api } from "@/lib/api"
import type {
  FollowUpBehavior,
  ThreadVisibility,
  UserPreferences,
} from "@/lib/api"
import { useTheme } from "@/lib/theme"

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

// Radix's Select rejects an empty-string item value, so "no default" needs a
// sentinel that is translated back to null on save.
const NO_DEFAULT_WORKSPACE = "__no_default_workspace__"

const PREFERENCES_KEY = ["myPreferences"]
const SAVE_PREFERENCES_KEY = ["saveMyPreferences"]

export function PreferencesSection() {
  const { theme, setTheme } = useTheme()
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
  const workspaceOptions = useQuery({
    queryKey: ["workspace-options"],
    queryFn: api.listWorkspaceOptions,
    staleTime: 60_000,
  })
  const workspaceItems = [
    { value: NO_DEFAULT_WORKSPACE, label: "Workspace default" },
    ...(workspaceOptions.data?.workspaces ?? []).map((workspace) => ({
      value: workspace.slug,
      label: workspace.name,
    })),
  ]
  const archiveThreads = useMutation({
    meta: { errorTitle: "Couldn't archive threads" },
    mutationFn: agentsApi.resolveAllThreads,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-threads"] }),
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
    <SettingsSection title="Preferences">
      <SettingsRow
        label="Appearance"
        description="Theme used across the dashboard."
        control={
          <Select
            items={THEMES}
            value={theme}
            onValueChange={(v) => v && setTheme(v)}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {THEMES.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <SettingsRow
        label="Default thread visibility"
        description="Preselected when you start a cloud thread. Private threads can use your personal integrations and only you can prompt them; workspace threads are open to everyone and run without personal credentials. Visibility cannot change after a thread is created."
        control={
          <Select
            items={VISIBILITIES}
            value={preferences.data?.default_visibility ?? "private"}
            onValueChange={(v) =>
              v &&
              savePreferences.mutate({
                default_visibility: v,
              })
            }
            disabled={preferences.isLoading}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {VISIBILITIES.map((v) => (
                <SelectItem key={v.value} value={v.value}>
                  {v.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <SettingsRow
        label="Default workspace"
        description="Preselected in the composer's workspace picker when the chosen repository does not belong to another workspace."
        control={
          <Select
            items={workspaceItems}
            value={preferences.data?.default_workspace ?? NO_DEFAULT_WORKSPACE}
            onValueChange={(v) =>
              v &&
              savePreferences.mutate({
                default_workspace: v === NO_DEFAULT_WORKSPACE ? null : v,
              })
            }
            disabled={preferences.isLoading || workspaceOptions.isLoading}
          >
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {workspaceItems.map((workspace) => (
                <SelectItem key={workspace.value} value={workspace.value}>
                  {workspace.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <SettingsRow
        label="Follow-up behavior"
        description="Queue follow-ups until the run ends, or steer the current run with them. ⌘↵ does the opposite for one message; Enter on an empty composer sends the next queued message now."
        control={
          <Select
            items={FOLLOW_UP_BEHAVIORS}
            value={preferences.data?.follow_up_behavior ?? "steer"}
            onValueChange={(v) =>
              v &&
              savePreferences.mutate({
                follow_up_behavior: v,
              })
            }
            disabled={preferences.isLoading}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FOLLOW_UP_BEHAVIORS.map((behavior) => (
                <SelectItem key={behavior.value} value={behavior.value}>
                  {behavior.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      <SettingsRow
        label="Local tracing project"
        description="Project used for local desktop runs. Leave blank to use the shared cloud project. Restart the desktop app after changing it."
        control={
          <Input
            className="w-56"
            placeholder={
              preferences.data?.default_local_tracing_project ??
              "Shared cloud project"
            }
            defaultValue={preferences.data?.local_tracing_project ?? ""}
            disabled={preferences.isLoading}
            onBlur={(event) =>
              savePreferences.mutate({
                local_tracing_project: event.target.value.trim() || null,
              })
            }
          />
        }
      />
      <SettingsRow
        label="Archive all threads"
        description={
          archiveThreads.isSuccess
            ? `${archiveThreads.data.resolved} threads archived.`
            : "Resolve all threads you have participated in for a clean slate. You can still find them in the resolved view."
        }
        control={
          <Button
            size="sm"
            variant="outline"
            disabled={archiveThreads.isPending}
            onClick={() => {
              if (
                window.confirm(
                  "Archive all your threads? They will remain available in the resolved view."
                )
              ) {
                archiveThreads.mutate()
              }
            }}
          >
            {archiveThreads.isPending ? "Archiving…" : "Archive all"}
          </Button>
        }
      />
      <SettingsRow
        label="Desktop notifications"
        description={
          !supported
            ? "Your browser does not support desktop notifications."
            : denied
              ? "Permission was denied. Re-enable it in your browser's site settings."
              : "Show a notification when an agent run finishes."
        }
        control={
          <Switch
            checked={enabled}
            onCheckedChange={(v) => void toggleNotifications(v)}
            disabled={!supported || denied}
          />
        }
      />
    </SettingsSection>
  )
}
