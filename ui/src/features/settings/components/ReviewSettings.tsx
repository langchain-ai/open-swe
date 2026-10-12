import { useEffect, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Switch } from "@langchain/macaw-components/Switch"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { SettingsSection } from "@/components/AppShell"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { TierRow } from "./WorkspaceSettingsSections"

/** Review guidelines and toggles at one tier: the instance, or one workspace's overrides. */
export function ReviewSettings({
  scope,
  canEdit,
}: {
  scope: SettingsScope
  canEdit: boolean
}) {
  const settings = useScopedSettings(scope)
  const [guidelinesDraft, setGuidelinesDraft] = useState("")

  const guidelinesValue = settings.saved?.org_guidelines ?? ""

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setGuidelinesDraft(guidelinesValue)
  }, [guidelinesValue])

  // Until the settings arrive a toggle would write a value nobody chose.
  const editable = canEdit && settings.data !== undefined
  const scoped = scope.kind === "workspace"
  const guidelinesInherited = settings.inherits("org_guidelines")

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
        description={
          scoped
            ? "Instructions injected into every review this workspace runs, across all of its repositories. Until this workspace sets its own, the instance guidelines apply. Repository-specific style prompts take precedence when they conflict."
            : "Instructions injected into every review, in every workspace that does not set its own. Repository-specific style prompts take precedence when they conflict."
        }
      >
        <div className="flex flex-col gap-space-2 p-space-4">
          {scoped && (
            <p className="text-xs text-secondary">
              {guidelinesInherited
                ? "Inherited from the instance."
                : "Overridden for this workspace."}
            </p>
          )}
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
              {scoped && !guidelinesInherited && (
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
                  disabled={!editable}
                  onClick={() => settings.reset("org_guidelines")}
                >
                  Reset to instance
                </Button>
              )}
              {guidelinesDirty && (
                <span className="text-xs text-secondary">Unsaved changes</span>
              )}
            </div>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title="Review Configuration">
        <div className="divide-y divide-default">
          <TierRow
            settings={settings}
            fields={["review_draft_prs"]}
            label="Review Draft PRs"
            description="Whether Open SWE Review runs on draft PRs. Each user can override it in their Git settings."
            control={toggle("review_draft_prs", "Review Draft PRs")}
          />
          <TierRow
            settings={settings}
            fields={["pr_summaries"]}
            label="PR Summaries"
            description="Generate descriptions on pull requests"
            control={toggle("pr_summaries", "PR Summaries")}
          />
          <TierRow
            settings={settings}
            fields={["review_trace_links"]}
            label="Reviewer trace links"
            description="Link each review comment to the reviewer's own LangSmith run. Only members of your LangSmith workspace can open it."
            control={toggle("review_trace_links", "Reviewer trace links")}
          />
        </div>
      </SettingsSection>

      {!canEdit && (
        <p className="text-xs text-secondary">
          These settings are read-only. Ask a workspace admin to change them.
        </p>
      )}
    </>
  )
}
