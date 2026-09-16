import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { TeamSettings } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"

const DEFAULT_SETTINGS: TeamSettings = {
  review_draft_prs: false,
  pr_summaries: true,
  review_trace_links: true,
  org_guidelines: null,
  default_agent_model: null,
  default_agent_reasoning_effort: null,
  default_agent_subagent_model: null,
  default_agent_subagent_reasoning_effort: null,
  default_reviewer_model: null,
  default_reviewer_reasoning_effort: null,
  default_reviewer_subagent_model: null,
  default_reviewer_subagent_reasoning_effort: null,
}

/** The review settings of one workspace: guidelines and the toggles. */
export function ReviewTeamSettings({
  workspace,
  canEdit,
}: {
  workspace: string
  canEdit: boolean
}) {
  const qc = useQueryClient()
  const settings = useQuery({
    queryKey: ["teamSettings", workspace],
    queryFn: () => api.getTeamSettings(workspace),
  })
  const [local, setLocal] = useState<TeamSettings>(DEFAULT_SETTINGS)
  const [guidelinesDraft, setGuidelinesDraft] = useState("")
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (settings.data) {
      // oxlint-disable-next-line react/set-state-in-effect
      setLocal(settings.data)
      setGuidelinesDraft(settings.data.org_guidelines ?? "")
    }
  }, [settings.data])

  const save = useMutation({
    mutationFn: (body: TeamSettings) => api.saveTeamSettings(body, workspace),
    onSuccess: (saved) => {
      qc.setQueryData(["teamSettings", workspace], saved)
      setError(null)
    },
    onError: (e: Error) => setError(e.message),
  })

  const current: TeamSettings = local
  // Until this workspace's settings arrive, `local` still holds the defaults,
  // so a toggle would write a value nobody chose.
  const editable = canEdit && !settings.isPending

  const persist = (patch: Partial<TeamSettings>) => {
    const next: TeamSettings = { ...current, ...patch }
    setLocal(next)
    if (editable) save.mutate(next)
  }

  const trimmedGuidelines = guidelinesDraft.trim()
  const savedGuidelines = current.org_guidelines ?? ""
  const guidelinesDirty = trimmedGuidelines !== savedGuidelines.trim()

  const saveGuidelines = () => {
    if (!editable) return
    persist({ org_guidelines: trimmedGuidelines || null })
  }

  return (
    <>
      <SettingsSection
        title="Review Guidelines"
        description="Instructions injected into every review this workspace runs, across all of its repositories. Repository-specific style prompts take precedence when they conflict."
      >
        <div className="flex flex-col gap-2 p-4">
          <Textarea
            className="min-h-[200px] w-full font-mono text-xs"
            value={guidelinesDraft}
            onChange={(e) => setGuidelinesDraft(e.target.value)}
            placeholder="e.g. Always flag missing input validation on new API endpoints. Prefer structured logging over print statements."
            disabled={!editable}
          />
          {canEdit && (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!editable || !guidelinesDirty || save.isPending}
                onClick={saveGuidelines}
              >
                Save guidelines
              </Button>
              {guidelinesDirty && (
                <span className="text-xs text-muted-foreground">
                  Unsaved changes
                </span>
              )}
            </div>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title="Configuration">
        <div className="divide-y divide-border">
          <SettingsRow
            label="Review Draft PRs"
            description="This workspace's default for whether Open SWE Review runs on draft PRs. Each user can override it in Profile Settings."
            control={
              <Switch
                checked={current.review_draft_prs}
                onCheckedChange={(v) => persist({ review_draft_prs: v })}
                disabled={!editable}
              />
            }
          />
          <SettingsRow
            label="PR Summaries"
            description="Generate descriptions on pull requests"
            control={
              <Switch
                checked={current.pr_summaries}
                onCheckedChange={(v) => persist({ pr_summaries: v })}
                disabled={!editable}
              />
            }
          />
          <SettingsRow
            label="Reviewer trace links"
            description="Link each review comment to the reviewer's own LangSmith run. Only members of your LangSmith workspace can open it."
            control={
              <Switch
                checked={current.review_trace_links}
                onCheckedChange={(v) => persist({ review_trace_links: v })}
                disabled={!editable}
              />
            }
          />
        </div>
      </SettingsSection>

      {!canEdit && (
        <p className="text-xs text-muted-foreground">
          These settings are read-only. Ask a workspace admin to change them.
        </p>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
    </>
  )
}
