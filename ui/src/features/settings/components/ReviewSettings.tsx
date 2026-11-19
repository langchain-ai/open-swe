import { useEffect, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Switch } from "@langchain/macaw-components/Switch"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { useInstanceSettings } from "@/features/settings/lib/settingsScope"
import { ReviewAppsSettings, ReviewBrowserSettings } from "./ReviewAppsSettings"

/** Instance review guidelines and toggles. */
export function ReviewSettings({ canEdit }: { canEdit: boolean }) {
  const settings = useInstanceSettings()
  const [guidelinesDraft, setGuidelinesDraft] = useState("")

  const guidelinesValue = settings.saved?.org_guidelines ?? ""

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setGuidelinesDraft(guidelinesValue)
  }, [guidelinesValue])

  // Until the settings arrive a toggle would write a value nobody chose.
  const editable = canEdit && settings.data !== undefined

  const trimmedGuidelines = guidelinesDraft.trim()
  const savedGuidelines = (settings.data?.org_guidelines ?? "").trim()
  const guidelinesDirty = trimmedGuidelines !== savedGuidelines

  const toggle = (
    field: "review_draft_prs" | "pr_summaries" | "review_trace_links",
    label: string
  ) => (
    <Switch
      aria-label={label}
      checked={!!settings.data?.[field]}
      onChange={(v) => settings.save({ [field]: v })}
      disabled={!editable}
    />
  )

  return (
    <>
      <SettingsSection
        title="Review Guidelines"
        description="Default instructions for reviews. Repository-specific style prompts take precedence when they conflict."
      >
        <div className="flex flex-col gap-space-2 p-space-4">
          <Textarea
            aria-label="Review guidelines"
            size="md"
            inputClassName="min-h-[200px] font-mono text-xs"
            value={guidelinesDraft}
            onChange={setGuidelinesDraft}
            placeholder="e.g. Always flag missing input validation on new API endpoints. Prefer structured logging over print statements."
            disabled={!editable}
          />
          {canEdit && (
            <div className="flex items-center gap-space-2">
              <Button
                size="xs"
                color="primary"
                disabled={!editable || !guidelinesDirty}
                onClick={() =>
                  settings.save({ org_guidelines: trimmedGuidelines || null })
                }
              >
                Save guidelines
              </Button>
              {guidelinesDirty && (
                <span className="text-xs text-secondary">Unsaved changes</span>
              )}
            </div>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title="Review Configuration">
        <div className="divide-y divide-default">
          <SettingsRow
            label="Review Draft PRs"
            description="Whether Robin Review runs on draft PRs. Each user can override it in their Git settings."
            control={toggle("review_draft_prs", "Review Draft PRs")}
          />
          <SettingsRow
            label="PR Summaries"
            description="Generate descriptions on pull requests"
            control={toggle("pr_summaries", "PR Summaries")}
          />
          <SettingsRow
            label="Reviewer trace links"
            description="Link each review comment to the reviewer's own LangSmith run. Only members of your LangSmith workspace can open it."
            control={toggle("review_trace_links", "Reviewer trace links")}
          />
        </div>
      </SettingsSection>

      <ReviewAppsSettings settings={settings} editable={editable} />
      <ReviewBrowserSettings settings={settings} editable={editable} />

      {!canEdit && (
        <p className="text-xs text-secondary">
          These settings are read-only. Ask a Robin admin to change them.
        </p>
      )}
    </>
  )
}
