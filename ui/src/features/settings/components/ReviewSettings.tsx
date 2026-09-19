import { useEffect, useState } from "react"
import { SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  useScopedSettings,
  type ScopedSettings,
  type SettingsScope,
} from "@/features/settings/lib/settingsScope"
import { TierRow } from "./WorkspaceSettingsSections"

interface ReviewSettingsProps {
  scope: SettingsScope
  canEdit: boolean
  onDirtyChange?: (dirty: boolean) => void
}

export function ReviewInstructionsSettings({
  scope,
  canEdit,
}: ReviewSettingsProps) {
  const settings = useScopedSettings(scope)
  return (
    <ReviewInstructionsSettingsView
      scope={scope}
      canEdit={canEdit}
      settings={settings}
    />
  )
}

export function ReviewInstructionsSettingsView({
  scope,
  canEdit,
  settings,
  onDirtyChange,
}: ReviewSettingsProps & { settings: ScopedSettings }) {
  const [dirty, setDirty] = useState(false)
  const [draft, setDraft] = useState("")
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange])
  const saved = settings.data?.org_guidelines ?? ""
  const [observedSaved, setObservedSaved] = useState<string | null>(null)
  if (dirty && draft.trim() === saved.trim()) {
    setDirty(false)
  } else if (!dirty && observedSaved !== saved) {
    setObservedSaved(saved)
    setDraft(saved)
  }
  const editable = canEdit && settings.data !== undefined
  const inherited = settings.inherits("org_guidelines")
  const trimmed = draft.trim()
  return (
    <SettingsSection
      title="Review guidelines"
      description={
        scope.kind === "workspace"
          ? "Instructions injected into every review this workspace runs. Repository instructions take precedence when they conflict."
          : "Shared instructions injected into every review. Repository instructions take precedence when they conflict."
      }
    >
      <div className="flex flex-col gap-2 p-4">
        {scope.kind === "workspace" && (
          <p className="text-xs text-muted-foreground">
            {inherited
              ? "Inherited from the instance."
              : "Overridden for this workspace."}
          </p>
        )}
        <Textarea
          aria-label="Shared review guidelines"
          className="min-h-[200px] w-full font-mono text-xs"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value)
            setDirty(true)
          }}
          disabled={!editable || settings.saving}
          placeholder="e.g. Always flag missing input validation on new API endpoints."
        />
        {canEdit && (
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={!editable || !dirty || settings.saving}
              onClick={() => settings.save({ org_guidelines: trimmed || null })}
            >
              Save guidelines
            </Button>
            {scope.kind === "workspace" && settings.data && !inherited && (
              <Button
                size="sm"
                variant="ghost"
                disabled={!editable || settings.saving}
                onClick={() => {
                  void settings
                    .resetAndWait("org_guidelines")
                    .then((effective) => {
                      if (!effective) return
                      const inheritedGuidelines = effective.org_guidelines ?? ""
                      setObservedSaved(inheritedGuidelines)
                      setDraft(inheritedGuidelines)
                      setDirty(false)
                    })
                    .catch(() => undefined)
                }}
              >
                Reset to instance
              </Button>
            )}
            {dirty && (
              <Button
                size="sm"
                variant="ghost"
                disabled={settings.saving}
                onClick={() => {
                  setDraft(settings.data?.org_guidelines ?? "")
                  setDirty(false)
                }}
              >
                Reload saved
              </Button>
            )}
            {dirty && (
              <span className="text-xs text-muted-foreground">
                Unsaved changes
              </span>
            )}
          </div>
        )}
        {settings.error && (
          <p className="text-xs text-destructive">{settings.error}</p>
        )}
      </div>
    </SettingsSection>
  )
}

export function ReviewAutomationSettings({
  scope,
  canEdit,
}: ReviewSettingsProps) {
  const settings = useScopedSettings(scope)
  return (
    <ReviewAutomationSettingsView
      scope={scope}
      canEdit={canEdit}
      settings={settings}
    />
  )
}

export function ReviewAutomationSettingsView({
  canEdit,
  settings,
}: ReviewSettingsProps & { settings: ScopedSettings }) {
  const editable = canEdit && settings.data !== undefined
  const toggle = (
    field: "review_draft_prs" | "pr_summaries" | "review_trace_links"
  ) => (
    <Switch
      checked={!!settings.data?.[field]}
      onCheckedChange={(value) => settings.save({ [field]: value })}
      disabled={!editable || settings.saving}
    />
  )
  return (
    <SettingsSection title="Review automation">
      <div className="divide-y divide-border">
        <TierRow
          settings={settings}
          fields={["review_draft_prs"]}
          label="Review Draft PRs"
          description="Whether Open SWE Review runs on draft PRs."
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
          description="Link review comments to the reviewer LangSmith run."
          control={toggle("review_trace_links")}
        />
      </div>
      {!canEdit && (
        <p className="p-4 text-xs text-muted-foreground">
          These settings are read-only.
        </p>
      )}
      {settings.error && (
        <p className="p-4 text-xs text-destructive">{settings.error}</p>
      )}
    </SettingsSection>
  )
}

/** Compatibility layout used by existing admin and workspace settings pages. */
export function ReviewSettings(props: ReviewSettingsProps) {
  const settings = useScopedSettings(props.scope)
  return (
    <>
      <ReviewInstructionsSettingsView {...props} settings={settings} />
      <ReviewAutomationSettingsView {...props} settings={settings} />
    </>
  )
}
