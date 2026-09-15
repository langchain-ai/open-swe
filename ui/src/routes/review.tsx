import { Link, createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CaretRightIcon } from "@phosphor-icons/react"
import { useEffect, useMemo, useState } from "react"
import { IoLogoGithub } from "react-icons/io5"

import type { TeamSettings } from "@/lib/api"
import {
  AppShell,
  SettingsNavRow,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { api, DEFAULT_WORKSPACE_SLUG } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { useWorkspaceOptions } from "@/features/agents/lib/queries"
import { WorkspaceSelect } from "@/features/settings/components/WorkspaceSelect"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/review")({ component: ReviewPage })

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

function ReviewPage() {
  const session = useSession()
  const workspaceOptions = useWorkspaceOptions(!!session.data)
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(
    null
  )
  // Falls back to the deployment default while the workspace list loads, or
  // until the user picks one.
  const workspace =
    selectedWorkspace ??
    workspaceOptions.data?.default_slug ??
    DEFAULT_WORKSPACE_SLUG

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  const canEdit = session.data.is_admin

  return (
    <AppShell
      user={session.data}
      title="Open SWE Review"
      description="Review pull requests for bugs and issues on demand, or run reviews automatically. Runs are billed based on underlying agent usage."
    >
      <RepositoriesSection canEdit={canEdit} />

      <SettingsSection title="Rules">
        <SettingsNavRow
          to="/review/styles"
          label="Review Style Prompts"
          description="Per-repo style guides learned from past PR review feedback."
        />
      </SettingsSection>

      <WorkspaceSelect
        workspaces={workspaceOptions.data?.workspaces ?? []}
        value={workspace}
        onChange={setSelectedWorkspace}
      />
      {/* Keyed by workspace so a switch remounts: half-made edits belong to
          the workspace they were typed for, never to the next one. */}
      <ReviewTeamSettings
        key={workspace}
        workspace={workspace}
        canEdit={canEdit}
      />
    </AppShell>
  )
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

function RepositoriesSection({ canEdit: _canEdit }: { canEdit: boolean }) {
  const repos = useRepos()

  const autoReview = useQuery({
    queryKey: ["autoReviewRepos"],
    queryFn: api.listAutoReviewRepos,
  })

  const autoReviewSet = useMemo(
    () => new Set(autoReview.data?.repos ?? []),
    [autoReview.data?.repos]
  )

  const grouped = useMemo(() => {
    const byOwner = new Map<
      string,
      Array<{ full_name: string; private: boolean }>
    >()
    for (const r of repos.data?.repositories ?? []) {
      const [owner] = r.full_name.split("/")
      if (!owner) continue
      const arr = byOwner.get(owner) ?? []
      arr.push(r)
      byOwner.set(owner, arr)
    }
    return Array.from(byOwner.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [repos.data?.repositories])

  const loading = repos.isLoading || autoReview.isLoading

  return (
    <SettingsSection
      title="Repositories"
      description="All installed repositories support on-demand reviews. Click into an installation to configure automatic reviews."
    >
      <div className="divide-y divide-border">
        {loading && (
          <div className="p-4">
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {!loading && grouped.length === 0 && (
          <p className="px-4 py-3 text-xs text-muted-foreground">
            No GitHub App installations found. Install the Open SWE GitHub App
            on an account or org to manage repos here.
          </p>
        )}
        {grouped.map(([owner, list]) => {
          const autoReviewCount = list.filter((r) =>
            autoReviewSet.has(r.full_name)
          ).length
          return (
            <Link
              key={owner}
              to="/review/repositories/$owner"
              params={{ owner }}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-muted/40"
            >
              <div className="flex items-center gap-3">
                <IoLogoGithub className="size-5 shrink-0 text-muted-foreground" />
                <div className="flex flex-col gap-0.5">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-medium text-foreground">{owner}</span>
                  </div>
                  <span className="text-xs text-muted-foreground">GitHub</span>
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>
                  {autoReviewCount}/{list.length} Run Automatically
                </span>
                <CaretRightIcon className="size-3.5" />
              </div>
            </Link>
          )
        })}
      </div>
    </SettingsSection>
  )
}
