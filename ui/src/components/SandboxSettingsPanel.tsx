import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, isGithubReauthError } from "@/lib/api"

export function SandboxSettingsPanel() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState<string | null>(null)

  const settings = useQuery({
    queryKey: ["sandboxSettings"],
    queryFn: api.getSandboxSettings,
  })

  const save = useMutation({
    mutationFn: (value: string | null) => api.saveSandboxSettings(value),
    onSuccess: (next) => {
      qc.setQueryData(["sandboxSettings"], next)
      setDraft(null)
      setError(null)
    },
    onError: (e: Error) =>
      setError(
        isGithubReauthError(e)
          ? "GitHub token expired — sign in again."
          : e.message
      ),
  })

  const base = settings.data
  const value = draft ?? base?.base_snapshot_id ?? ""
  const dirty = value.trim() !== (base?.base_snapshot_id ?? "")
  const hint =
    base?.base_snapshot_source === "admin"
      ? `Overrides DEFAULT_SANDBOX_SNAPSHOT_ID${
          base.env_base_snapshot_id ? ` (${base.env_base_snapshot_id})` : ""
        }. New sandboxes boot from this unless their environment has a ready snapshot.`
      : base?.base_snapshot_source === "env"
        ? "Using DEFAULT_SANDBOX_SNAPSHOT_ID. Set a value here to change it without a redeploy."
        : "No base snapshot configured — new sandboxes boot from the LangSmith default snapshot (git, gh, Python and Node preinstalled). Set one here or in DEFAULT_SANDBOX_SNAPSHOT_ID to use a custom image."

  return (
    <div className="flex flex-col gap-2 p-4">
      <Label htmlFor="base-snapshot">Base snapshot</Label>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Input
          id="base-snapshot"
          placeholder={base?.env_base_snapshot_id ?? "snapshot id"}
          value={value}
          onChange={(e) => setDraft(e.target.value)}
          disabled={settings.isLoading}
          className="sm:flex-1"
        />
        <Button
          size="sm"
          className="shrink-0 sm:w-auto"
          disabled={!dirty || save.isPending}
          onClick={() => void save.mutateAsync(value.trim() || null)}
        >
          Save
        </Button>
        {base?.base_snapshot_id && (
          <Button
            size="sm"
            variant="secondary"
            className="shrink-0 sm:w-auto"
            disabled={save.isPending}
            onClick={() => void save.mutateAsync(null)}
          >
            Use env default
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{hint}</p>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
