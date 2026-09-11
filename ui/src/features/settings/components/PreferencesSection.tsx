import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
import { Switch } from "@/components/ui/switch"
import {
  notificationsEnabled,
  notificationsSupported,
  requestNotificationPermission,
  setNotificationsPref,
} from "@/lib/notifications"
import { agentsApi } from "@/features/agents/lib/api"
import { api } from "@/lib/api"
import type { ThreadVisibility } from "@/lib/api"
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

export function PreferencesSection() {
  const { theme, setTheme } = useTheme()
  const qc = useQueryClient()
  const preferences = useQuery({
    queryKey: ["myPreferences"],
    queryFn: api.getMyPreferences,
  })
  const savePreferences = useMutation({
    mutationFn: (default_visibility: ThreadVisibility) =>
      api.saveMyPreferences({ default_visibility }),
    onSuccess: (data) => qc.setQueryData(["myPreferences"], data),
  })
  const archiveThreads = useMutation({
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
          <Select value={theme} onValueChange={(v) => v && setTheme(v)}>
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
        description={
          savePreferences.error
            ? `Could not save: ${savePreferences.error.message}`
            : "Preselected when you start a cloud thread. Private threads can use your personal integrations and only you can prompt them; workspace threads are open to everyone and run without personal credentials. Visibility cannot change after a thread is created."
        }
        control={
          <Select
            value={preferences.data?.default_visibility ?? "private"}
            onValueChange={(v) => v && savePreferences.mutate(v)}
            disabled={preferences.isLoading || savePreferences.isPending}
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
        label="Archive all threads"
        description={
          archiveThreads.error
            ? `Could not archive threads: ${archiveThreads.error.message}`
            : archiveThreads.isSuccess
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
