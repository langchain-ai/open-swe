import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { SettingsSection } from "@/components/AppShell"

export function LocalMCPSection() {
  const desktop =
    typeof window === "undefined" ? undefined : window.openSweDesktop
  const [config, setConfig] = useState("")
  const [configPath, setConfigPath] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void desktop?.getMcpConfig().then(
      ({ text, path }) => {
        setConfig(text)
        setConfigPath(path)
      },
      (cause) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Unable to read MCP settings."
        )
    )
  }, [desktop])

  if (!desktop) return null

  return (
    <SettingsSection
      title="Local MCPs"
      description="Configure MCP servers used by local desktop runs. Local commands execute on this computer, so only add servers you trust."
    >
      <form
        className="space-y-3 p-4"
        onSubmit={(event) => {
          event.preventDefault()
          setSaving(true)
          setSaved(false)
          setError(null)
          void desktop.saveMcpConfig(config).then(
            ({ text, path }) => {
              setConfig(text)
              setConfigPath(path)
              setSaved(true)
              setSaving(false)
            },
            (cause) => {
              setError(
                cause instanceof Error
                  ? cause.message
                  : "Unable to save MCP settings."
              )
              setSaving(false)
            }
          )
        }}
      >
        <Textarea
          aria-label="Local MCP configuration JSON"
          className="h-64 max-h-[32rem] resize-y font-mono"
          value={config}
          onChange={(event) => {
            setConfig(event.target.value)
            setSaved(false)
          }}
          autoComplete="off"
          spellCheck={false}
        />
        {configPath && (
          <p className="text-xs break-all text-muted-foreground">
            {configPath}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {saved && <p className="text-sm text-muted-foreground">Saved.</p>}
        <Button type="submit" size="sm" disabled={saving || !config.trim()}>
          {saving ? "Saving…" : "Save local MCPs"}
        </Button>
      </form>
    </SettingsSection>
  )
}
