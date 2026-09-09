import { useEffect, useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export function DesktopBackendSection() {
  const desktop = window.openSweDesktop
  const [saved, setSaved] = useState("")
  const [value, setValue] = useState("")
  const [loading, setLoading] = useState(!!desktop)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!desktop) return
    void desktop
      .getBackendUrl()
      .then((url) => {
        setSaved(url)
        setValue(url)
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => setLoading(false))
  }, [desktop])

  if (!desktop) return null

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const result = await desktop.setBackendUrl(value)
      setSaved(result.backendUrl)
      setValue(result.backendUrl)
      if (result.changed) window.location.reload()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
    setSaving(false)
  }

  return (
    <SettingsSection
      title="Desktop"
      description="Configuration stored by the Open SWE desktop app on this computer."
    >
      <SettingsRow
        label="Backend URL"
        description="Changing deployments signs you out of the current deployment."
        htmlFor="desktop-backend-url"
        control={
          <div className="flex w-full flex-col gap-1.5 sm:w-80">
            <div className="flex gap-2">
              <Input
                id="desktop-backend-url"
                type="url"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                disabled={loading || saving}
              />
              <Button
                onClick={() => void save()}
                disabled={loading || saving || value.trim() === saved}
              >
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
            {error && <p className="text-xs text-destructive">{error}</p>}
          </div>
        }
      />
    </SettingsSection>
  )
}
