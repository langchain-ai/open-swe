import { useEffect, useState } from "react"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { SettingSection } from "@langchain/gtm-platform-design-system/patterns/setting-section"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { Lock } from "@/components/glyphs"
import {
  useScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { TierRow } from "./WorkspaceSettingsSections"

type ReviewToggle = "review_draft_prs" | "pr_summaries" | "review_trace_links"

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

  const toggleRow = (
    field: ReviewToggle,
    label: string,
    description: string
  ) => (
    <TierRow
      settings={settings}
      fields={[field]}
      label={label}
      description={description}
      density="compact"
      control={(slot) => (
        <Switch
          id={slot.id}
          aria-describedby={slot.describedById}
          checked={!!settings.data?.[field]}
          onCheckedChange={(v) => settings.save({ [field]: v })}
          disabled={!editable}
        />
      )}
    />
  )

  return (
    <>
      <PageSection
        contained
        inset="padded"
        title="Review Guidelines"
        description={
          scoped
            ? "Instructions injected into every review this workspace runs, across all of its repositories. Until this workspace sets its own, the instance guidelines apply. Repository-specific style prompts take precedence when they conflict."
            : "Instructions injected into every review, in every workspace that does not set its own. Repository-specific style prompts take precedence when they conflict."
        }
        actions={
          scoped ? (
            <Badge
              tier="quiet"
              tone={guidelinesInherited ? "neutral" : "attention"}
            >
              {guidelinesInherited
                ? "Inherited from the instance."
                : "Overridden for this workspace."}
            </Badge>
          ) : undefined
        }
      >
        <Textarea
          aria-label="Review guidelines"
          className="min-h-50 w-full font-mono"
          value={guidelinesDraft}
          onChange={(e) => setGuidelinesDraft(e.target.value)}
          placeholder="e.g. Always flag missing input validation on new API endpoints. Prefer structured logging over print statements."
          disabled={!editable}
        />
        {canEdit && (
          <Inline gap="sm" align="center" wrap>
            <Button
              size="compact"
              disabled={!editable || !guidelinesDirty}
              onClick={() =>
                settings.save({ org_guidelines: trimmedGuidelines || null })
              }
            >
              Save guidelines
            </Button>
            {scoped && !guidelinesInherited && (
              <Button
                size="compact"
                variant="ghost"
                disabled={!editable}
                onClick={() => settings.reset("org_guidelines")}
              >
                Reset to instance
              </Button>
            )}
            {guidelinesDirty && (
              <Box render={<span />} className="text-meta text-ink-subtle">
                Unsaved changes
              </Box>
            )}
          </Inline>
        )}
      </PageSection>

      <SettingSection contained title="Review configuration">
        {toggleRow(
          "review_draft_prs",
          "Review Draft PRs",
          "Whether Open SWE Review runs on draft PRs. Each user can override it in their Git settings."
        )}
        {toggleRow(
          "pr_summaries",
          "PR Summaries",
          "Generate descriptions on pull requests"
        )}
        {toggleRow(
          "review_trace_links",
          "Reviewer trace links",
          "Link each review comment to the reviewer's own LangSmith run. Only members of your LangSmith workspace can open it."
        )}
      </SettingSection>

      {!canEdit && (
        <Alert tone="neutral" icon={Lock}>
          <AlertDescription>
            These settings are read-only. Ask a workspace admin to change them.
          </AlertDescription>
        </Alert>
      )}
    </>
  )
}
