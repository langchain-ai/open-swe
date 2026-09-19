import { useEffect, useState } from "react"
import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
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

  useEffect(() => {
    if (settings.data) {
      // oxlint-disable-next-line react/set-state-in-effect
      setGuidelinesDraft(settings.data.org_guidelines ?? "")
    }
  }, [settings.data])

  // Until the settings arrive a toggle would write a value nobody chose.
  const editable = canEdit && settings.data !== undefined
  const scoped = scope.kind === "workspace"
  const guidelinesInherited = settings.inherits("org_guidelines")

  const trimmedGuidelines = guidelinesDraft.trim()
  const savedGuidelines = (settings.data?.org_guidelines ?? "").trim()
  const guidelinesDirty = trimmedGuidelines !== savedGuidelines

  const toggle = (
    field: "review_draft_prs" | "pr_summaries" | "review_trace_links"
  ) => (
    <Switch
      checked={!!settings.data?.[field]}
      onCheckedChange={(v) => settings.save({ [field]: v })}
      disabled={!editable || settings.saving}
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
        <div className="flex flex-col gap-2 p-4">
          {scoped && (
            <p className="text-xs text-muted-foreground">
              {guidelinesInherited
                ? "Inherited from the instance."
                : "Overridden for this workspace."}
            </p>
          )}
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
                disabled={!editable || !guidelinesDirty || settings.saving}
                onClick={() =>
                  settings.save({ org_guidelines: trimmedGuidelines || null })
                }
              >
                Save guidelines
              </Button>
              {scoped && !guidelinesInherited && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!editable || settings.saving}
                  onClick={() => settings.reset("org_guidelines")}
                >
                  Reset to instance
                </Button>
              )}
              {guidelinesDirty && (
                <span className="text-xs text-muted-foreground">
                  Unsaved changes
                </span>
              )}
            </div>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title="Review configuration">
        <div className="divide-y divide-border">
          <TierRow
            settings={settings}
            fields={["review_draft_prs"]}
            label="Review Draft PRs"
            description="Whether Open SWE Review runs on draft PRs. Each user can override it in Profile Settings."
            control={toggle("review_draft_prs")}
          />
          <TierRow
            settings={settings}
            fields={["pr_summaries"]}
            label="PR Summaries"
            description="Generate descriptions on pull requests"
            control={toggle("pr_summaries")}
          />
          <TierRow
            settings={settings}
            fields={["review_trace_links"]}
            label="Reviewer trace links"
            description="Link each review comment to the reviewer's own LangSmith run. Only members of your LangSmith workspace can open it."
            control={toggle("review_trace_links")}
          />
        </div>
      </SettingsSection>

      {!canEdit && (
        <p className="text-xs text-muted-foreground">
          These settings are read-only. Ask a workspace admin to change them.
        </p>
      )}

      {settings.error && (
        <p className="text-xs text-destructive">{settings.error}</p>
      )}
    </>
  )
}
