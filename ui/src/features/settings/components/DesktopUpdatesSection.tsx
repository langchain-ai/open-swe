import { useEffect, useState } from "react"

import type { DesktopUpdateChannel } from "@/desktop"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export function DesktopUpdatesSection() {
  const desktop = window.openSweDesktop
  const [channel, setChannel] = useState<DesktopUpdateChannel>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string>()

  useEffect(() => {
    void desktop
      ?.getUpdateChannel()
      .then(setChannel)
      .catch(() => undefined)
  }, [desktop])

  if (!desktop || !channel) return null

  const changeChannel = async (next: DesktopUpdateChannel) => {
    const previous = channel
    setChannel(next)
    setSaving(true)
    setError(undefined)
    try {
      setChannel(await desktop.setUpdateChannel(next))
    } catch (cause) {
      setChannel(previous)
      setError(
        cause instanceof Error ? cause.message : "Could not change channel"
      )
    }
    setSaving(false)
  }

  return (
    <SettingsSection title="Desktop updates">
      <SettingsRow
        label="Update channel"
        description={
          error ??
          "Nightly receives the latest desktop builds and may be less stable."
        }
        control={
          <Select
            value={channel}
            onValueChange={(value) =>
              value && void changeChannel(value as DesktopUpdateChannel)
            }
            disabled={saving}
          >
            <SelectTrigger className="w-40" aria-label="Update channel">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="stable">Stable</SelectItem>
              <SelectItem value="nightly">Nightly</SelectItem>
            </SelectContent>
          </Select>
        }
      />
    </SettingsSection>
  )
}
