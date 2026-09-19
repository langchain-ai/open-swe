import { useState } from "react"

import { Label } from "@/components/ui/label"
import { ApprovalPolicyPanel } from "@/features/reviews/components/ApprovalPolicyPanel"
import { RepositoryInstructionsPanel } from "@/features/reviews/components/RepositoryInstructionsPanel"
import {
  ReviewAutomationSettingsView,
  ReviewInstructionsSettingsView,
} from "@/features/settings/components/ReviewSettings"
import {
  INSTANCE_SCOPE,
  useScopedSettings,
} from "@/features/settings/lib/settingsScope"
import { useRepos } from "@/lib/profile"

export type ReviewSettingsTab = "instructions" | "approval" | "automation"

export function ReviewConfiguration({
  initialTab,
  initialRepository = null,
  canEdit,
  onTabChange,
  onRepositoryChange,
}: {
  initialTab: ReviewSettingsTab
  initialRepository?: string | null
  canEdit: boolean
  onTabChange?: (tab: ReviewSettingsTab) => void
  onRepositoryChange?: (repository: string | null) => void
}) {
  const repos = useRepos()
  const instanceSettings = useScopedSettings(INSTANCE_SCOPE)
  const [tab, setTab] = useState(initialTab)
  const activeTab = onTabChange ? initialTab : tab
  const [repository, setRepository] = useState(initialRepository)
  const [instructionsDirty, setInstructionsDirty] = useState(false)
  const [policyDirty, setPolicyDirty] = useState(false)
  const selectTab = (next: ReviewSettingsTab) => {
    if (onTabChange) onTabChange(next)
    else setTab(next)
  }
  const owner = repository?.split("/")[0]

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="space-y-2">
          <Label htmlFor="review-settings-scope">Review settings scope</Label>
          <select
            id="review-settings-scope"
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm sm:max-w-md"
            value={repository ?? ""}
            onChange={(event) => {
              const next = event.target.value || null
              if (next === repository) return
              if (
                (instructionsDirty || policyDirty) &&
                !window.confirm("Change scope and discard any unsaved changes?")
              )
                return
              setInstructionsDirty(false)
              setPolicyDirty(false)
              setRepository(next)
              onRepositoryChange?.(next)
            }}
          >
            <option value="">Shared default</option>
            {(repos.data?.repositories ?? []).map((repo) => (
              <option key={repo.full_name} value={repo.full_name}>
                {repo.full_name}
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            Instance defaults, with repository-specific review guidance and
            approval requirements. Existing workspace overrides continue to
            apply.
          </p>
          {repos.isError && (
            <p role="alert" className="text-xs text-destructive">
              Could not load accessible repositories. Reconnect GitHub or try
              again.
            </p>
          )}
        </div>
        <div className="mt-4 flex gap-4 border-b border-border" role="tablist">
          {(["instructions", "approval", "automation"] as const).map(
            (value) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={activeTab === value}
                className={`border-b-2 px-1 py-2 text-sm ${activeTab === value ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
                onClick={() => selectTab(value)}
              >
                {value === "approval"
                  ? "Approval policy"
                  : value[0]!.toUpperCase() + value.slice(1)}
              </button>
            )
          )}
        </div>
      </div>

      <div
        className={repository ? "rounded-lg border border-border bg-card" : ""}
        hidden={activeTab !== "instructions"}
      >
        {repository ? (
          <RepositoryInstructionsPanel
            key={repository}
            repository={repository}
            canEdit={canEdit}
            onDirtyChange={setInstructionsDirty}
          />
        ) : (
          <ReviewInstructionsSettingsView
            scope={INSTANCE_SCOPE}
            canEdit={canEdit}
            settings={instanceSettings}
            onDirtyChange={setInstructionsDirty}
          />
        )}
      </div>
      <div
        className="rounded-lg border border-border bg-card"
        hidden={activeTab !== "approval"}
      >
        <ApprovalPolicyPanel
          key={repository ?? "shared"}
          repository={repository}
          onDirtyChange={setPolicyDirty}
        />
      </div>
      <div
        className={repository ? "rounded-lg border border-border bg-card" : ""}
        hidden={activeTab !== "automation"}
      >
        {repository && owner ? (
          <div className="space-y-3 p-4">
            <h2 className="text-sm font-medium">Repository automation</h2>
            <p className="text-xs text-muted-foreground">
              Choose when Open SWE automatically reviews pull requests for{" "}
              {repository}.
            </p>
            <a
              className="text-sm font-medium text-primary underline underline-offset-4"
              href={`/review/repositories/${encodeURIComponent(owner)}`}
            >
              Manage automatic reviews for {repository}
            </a>
          </div>
        ) : (
          <ReviewAutomationSettingsView
            scope={INSTANCE_SCOPE}
            canEdit={canEdit}
            settings={instanceSettings}
          />
        )}
      </div>
    </div>
  )
}
