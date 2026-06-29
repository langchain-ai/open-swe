import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type {
  WorkspaceRepositorySettings,
  WorkspaceRepositorySettingsUpdateBody,
} from "@/lib/api"
import { api } from "@/lib/api"

function splitList(value: string): Array<string> {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
}

function joinList(value: Array<string> | undefined): string {
  return (value ?? []).join(", ")
}

function draftFromConfig(
  config: WorkspaceRepositorySettings | undefined
): WorkspaceRepositorySettingsUpdateBody {
  return {
    provider: "github",
    repositories: config?.repositories ?? [],
    default_repository: config?.default_repository ?? "",
    base_branch: config?.branch_policy.base_branch ?? "main",
    branch_prefix: config?.branch_policy.branch_prefix ?? "delivery",
    draft_pull_requests: config?.branch_policy.draft_pull_requests ?? true,
    allowed_actions: config?.credential_policy.allowed_actions?.length
      ? config.credential_policy.allowed_actions
      : ["branch", "commit", "pull_request"],
    context_repositories:
      config?.context_pack.repositories?.length
        ? config.context_pack.repositories
        : (config?.repositories ?? []),
    required_documents: config?.context_pack.required_documents ?? [],
  }
}

function AccessList({ config }: { config?: WorkspaceRepositorySettings }) {
  const access = config?.access ?? []
  if (!access.length) {
    return (
      <p className="px-4 pb-3 text-xs text-muted-foreground">
        No repositories configured.
      </p>
    )
  }
  return (
    <div className="divide-y divide-border border-t border-border">
      {access.map((repo) => (
        <div
          key={repo.full_name}
          className="grid gap-2 px-4 py-3 text-xs sm:grid-cols-[1fr_90px_1fr]"
        >
          <div className="min-w-0 truncate font-medium">{repo.full_name}</div>
          <Badge variant={repo.status === "ready" ? "default" : "destructive"}>
            {repo.status === "ready" ? "Ready" : "Blocked"}
          </Badge>
          <div className="min-w-0 truncate text-muted-foreground">
            {repo.message ?? (repo.default ? "Default repository" : "-")}
          </div>
        </div>
      ))}
    </div>
  )
}

export function WorkspaceRepositoriesSection({
  projectId,
}: {
  projectId: string
}) {
  const qc = useQueryClient()
  const config = useQuery({
    queryKey: ["workspaceRepositories", projectId],
    queryFn: () => api.getWorkspaceRepositories(projectId),
  })
  const [draft, setDraft] =
    useState<WorkspaceRepositorySettingsUpdateBody>(() =>
      draftFromConfig(undefined)
    )
  const draftRef = useRef(draft)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (config.data) {
      const nextDraft = draftFromConfig(config.data)
      draftRef.current = nextDraft
      setDraft(nextDraft)
    }
  }, [config.data])

  const updateDraft = (
    updater: (
      current: WorkspaceRepositorySettingsUpdateBody
    ) => WorkspaceRepositorySettingsUpdateBody
  ) => {
    setDraft((current) => {
      const nextDraft = updater(current)
      draftRef.current = nextDraft
      return nextDraft
    })
  }

  const save = useMutation({
    mutationFn: () => api.saveWorkspaceRepositories(projectId, draftRef.current),
    onSuccess: (next) => {
      setError(null)
      const nextDraft = draftFromConfig(next)
      draftRef.current = nextDraft
      setDraft(nextDraft)
      void qc.invalidateQueries({ queryKey: ["workspaceRepositories", projectId] })
      void qc.invalidateQueries({ queryKey: ["deliveryProjects"] })
      void qc.invalidateQueries({
        queryKey: ["deliveryProjectReadiness", projectId],
      })
    },
    onError: (e: Error) => setError(e.message),
  })
  const testAccess = useMutation({
    mutationFn: () => api.testWorkspaceRepositoryAccess(projectId),
    onSuccess: (next) => {
      setError(null)
      void qc.setQueryData(["workspaceRepositories", projectId], next)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <SettingsSection
      title="Repositories"
      description="Configure the GitHub repositories and branch policy used by delivery workers."
    >
      <div id="repositories" className="divide-y divide-border">
        <SettingsRow
          label="Provider"
          description="V1 delivery uses GitHub as version-control provider."
          control={<Badge variant="default">GitHub</Badge>}
        />
        <SettingsRow
          label="Repositories"
          description="Comma-separated repositories in owner/repo format."
          control={
            <Input
              aria-label="Workspace repositories"
              className="w-full sm:w-96"
              value={joinList(draft.repositories)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  repositories: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Default repository"
          description="Delivery workers create branches and draft PRs against this repository."
          control={
            <Input
              aria-label="Default repository"
              className="w-full sm:w-96"
              value={draft.default_repository}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  default_repository: e.target.value,
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Base branch"
          description="Branch used as delivery starting point."
          control={
            <Input
              aria-label="Base branch"
              className="w-full sm:w-60"
              value={draft.base_branch}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  base_branch: e.target.value,
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Branch prefix"
          description="Prefix for generated delivery branches."
          control={
            <Input
              aria-label="Branch prefix"
              className="w-full sm:w-60"
              value={draft.branch_prefix}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  branch_prefix: e.target.value,
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Draft PRs"
          description="Delivery workers should open pull requests as drafts."
          control={
            <input
              aria-label="Draft pull requests"
              type="checkbox"
              checked={draft.draft_pull_requests}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  draft_pull_requests: e.target.checked,
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Allowed actions"
          description="Comma-separated provider actions available to delivery workers."
          control={
            <Input
              aria-label="Allowed delivery actions"
              className="w-full sm:w-96"
              value={joinList(draft.allowed_actions)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  allowed_actions: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Context repositories"
          description="Repositories referenced by the workspace context pack."
          control={
            <Input
              aria-label="Context repositories"
              className="w-full sm:w-96"
              value={joinList(draft.context_repositories)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  context_repositories: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Required documents"
          description="Comma-separated context documents workers should receive."
          control={
            <Input
              aria-label="Required documents"
              className="w-full sm:w-96"
              value={joinList(draft.required_documents)}
              onChange={(e) =>
                updateDraft((current) => ({
                  ...current,
                  required_documents: splitList(e.target.value),
                }))
              }
            />
          }
        />
        <SettingsRow
          label="Actions"
          description="Save persists policy. Test access checks configured repositories with the current user credential."
          control={
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => testAccess.mutate()}
                disabled={testAccess.isPending || config.isLoading}
              >
                Test access
              </Button>
              <Button
                size="sm"
                onClick={() => save.mutate()}
                disabled={save.isPending || config.isLoading}
              >
                Save
              </Button>
            </div>
          }
        />
      </div>
      <AccessList config={config.data} />
      {error && <p className="px-4 pb-3 text-xs text-destructive">{error}</p>}
    </SettingsSection>
  )
}
