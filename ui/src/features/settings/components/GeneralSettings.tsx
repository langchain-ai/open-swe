import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useState } from "react"

import type { Theme } from "@/lib/theme"
import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import {
  SettingRow,
  SettingSection,
  type SettingValue,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@langchain/gtm-platform-design-system/ui/select"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import { ArchiveBox } from "@/components/glyphs"
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

const UNKNOWN_CHOICE = "That is not one of the options."

/** Narrows a row's saved value back to one of the options it offered. */
function choiceOf<T extends string>(
  options: ReadonlyArray<{ value: T }>,
  value: SettingValue
): T {
  const choice = options.find((option) => option.value === value)?.value
  if (choice === undefined) throw new Error(UNKNOWN_CHOICE)
  return choice
}

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
  // Each row reports its own failure inline, so the toast is suppressed.
  const savePreferences = useMutation({
    mutationKey: SAVE_PREFERENCES_KEY,
    scope: { id: SAVE_PREFERENCES_KEY.join(":") },
    meta: { errorTitle: "Couldn't save preferences", silent: true },
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
  const savePreference = async (patch: Partial<UserPreferences>) => {
    await savePreferences.mutateAsync(patch)
  }
  const workspaceOptions = useWorkspaceOptions()
  const workspaceItems = [
    { value: NO_DEFAULT_WORKSPACE, label: "Workspace default" },
    ...(workspaceOptions.data?.workspaces ?? []).map((workspace) => ({
      value: workspace.slug,
      label: workspace.name,
    })),
  ]
  // The confirmation dialog reports a failure inline, so the toast is suppressed.
  const archiveThreads = useMutation({
    meta: { errorTitle: "Couldn't archive threads", silent: true },
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
    <>
      <SettingSection title="Appearance" contained>
        <SettingRow
          label="Theme"
          description="Color theme for the dashboard on this device."
          control={(slot) => (
            <Select
              items={THEMES}
              value={theme}
              onValueChange={(v) => v && setTheme(v)}
            >
              <SelectTrigger
                id={slot.id}
                aria-describedby={slot.describedById}
                className="w-full"
              >
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
          )}
        />
      </SettingSection>

      <SettingSection
        title="Threads"
        description="Defaults for new threads and how follow-ups reach running ones. ⌘↵ does the opposite for one message; Enter on an empty composer sends the next queued message now."
        contained
      >
        <SettingRow
          label="Default visibility"
          description="Private threads can use your personal connections and only you can prompt them. Visibility can't change later."
          onSave={(value) =>
            savePreference({
              default_visibility: choiceOf(VISIBILITIES, value),
            })
          }
          control={(slot) => (
            <Select
              items={VISIBILITIES}
              value={preferences.data?.default_visibility ?? "private"}
              onValueChange={(v) => v && slot.commit(v)}
              disabled={preferences.isLoading || slot.saving}
            >
              <SelectTrigger
                id={slot.id}
                aria-describedby={slot.describedById}
                aria-invalid={slot.invalid}
                className="w-full"
              >
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
          )}
        />
        <SettingRow
          label="Default workspace"
          description="Preselected in the composer when the repository doesn't belong to another workspace."
          onSave={(value) => {
            const slug = choiceOf(workspaceItems, value)
            return savePreference({
              default_workspace: slug === NO_DEFAULT_WORKSPACE ? null : slug,
            })
          }}
          control={(slot) => (
            <Select
              items={workspaceItems}
              value={
                preferences.data?.default_workspace ?? NO_DEFAULT_WORKSPACE
              }
              onValueChange={(v) => v && slot.commit(v)}
              disabled={
                preferences.isLoading ||
                workspaceOptions.isLoading ||
                slot.saving
              }
            >
              <SelectTrigger
                id={slot.id}
                aria-describedby={slot.describedById}
                aria-invalid={slot.invalid}
                className="w-full"
              >
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
          )}
        />
        <SettingRow
          label="Follow-up behavior"
          description="Queue follow-ups until the run ends, or steer the current run with them."
          onSave={(value) =>
            savePreference({
              follow_up_behavior: choiceOf(FOLLOW_UP_BEHAVIORS, value),
            })
          }
          control={(slot) => (
            <Select
              items={FOLLOW_UP_BEHAVIORS}
              value={preferences.data?.follow_up_behavior ?? "steer"}
              onValueChange={(v) => v && slot.commit(v)}
              disabled={preferences.isLoading || slot.saving}
            >
              <SelectTrigger
                id={slot.id}
                aria-describedby={slot.describedById}
                aria-invalid={slot.invalid}
                className="w-full"
              >
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
          )}
        />
        <SettingRow
          density="compact"
          label="Collapse subagent threads"
          description="Keep nested subagent threads folded in the sidebar until you expand them."
          control={(slot) => (
            <Switch
              id={slot.id}
              aria-describedby={slot.describedById}
              checked={prefs.collapseSubagentsByDefault}
              onCheckedChange={setCollapseSubagentsByDefault}
            />
          )}
        />
      </SettingSection>

      <SettingSection title="Notifications" contained>
        <SettingRow
          density="compact"
          label="Desktop notifications"
          description={
            !supported
              ? "Your browser doesn't support desktop notifications."
              : denied
                ? "Permission was denied. Re-enable it in your browser's site settings."
                : "Notify me when an agent run finishes."
          }
          control={(slot) => (
            <Switch
              id={slot.id}
              aria-describedby={slot.describedById}
              checked={enabled}
              onCheckedChange={(v) => void toggleNotifications(v)}
              disabled={!supported || denied}
            />
          )}
        />
      </SettingSection>

      {typeof window !== "undefined" && window.openSweDesktop && (
        <SettingSection
          title="Desktop app"
          description="Restart the desktop app after changing these."
          contained
        >
          <SettingRow
            label="Local tracing project"
            description="Project for local desktop runs. Leave blank to use the shared cloud project."
            onSave={(value) =>
              savePreference({
                local_tracing_project: String(value).trim() || null,
              })
            }
            control={(slot) => (
              <Input
                id={slot.id}
                aria-describedby={slot.describedById}
                aria-invalid={slot.invalid}
                className="w-full"
                placeholder={
                  preferences.data?.default_local_tracing_project ??
                  "Shared cloud project"
                }
                defaultValue={preferences.data?.local_tracing_project ?? ""}
                disabled={preferences.isLoading}
                onBlur={(event) => slot.commit(event.target.value)}
              />
            )}
          />
        </SettingSection>
      )}

      <SettingSection title="Data" contained>
        <SettingRow
          label="Archive all threads"
          description={
            archiveThreads.isSuccess
              ? `${archiveThreads.data.resolved} threads archived.`
              : "Resolve every thread you've participated in. They stay in the resolved view."
          }
          control={(slot) => (
            <ConfirmableAction
              tone="PRIMARY"
              confirmIcon={ArchiveBox}
              title="Archive all your threads?"
              description="Every thread you've participated in is resolved. They remain available in the resolved view."
              confirmLabel="Archive all"
              onConfirm={async () => {
                await archiveThreads.mutateAsync()
              }}
              trigger={
                <Button
                  id={slot.id}
                  aria-describedby={slot.describedById}
                  size="compact"
                  variant="outline"
                  loading={archiveThreads.isPending}
                >
                  Archive all
                </Button>
              }
            />
          )}
        />
      </SettingSection>
    </>
  )
}
