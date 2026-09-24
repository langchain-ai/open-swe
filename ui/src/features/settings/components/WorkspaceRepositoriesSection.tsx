import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import type { RepositorySettings } from "@/lib/api"

export function WorkspaceRepositoriesSection({
  slug,
  canEdit,
}: {
  slug: string
  canEdit: boolean
}) {
  const queryKey = ["workspaceRepositories", slug]
  const qc = useQueryClient()
  const repositories = useQuery({
    queryKey,
    queryFn: () => api.listWorkspaceRepositories(slug),
  })
  const configure = useMutation({
    mutationFn: ({
      repo,
      mayStartThreads,
    }: {
      repo: string
      mayStartThreads: boolean
    }) =>
      api.configureWorkspaceRepository(slug, repo, {
        may_start_threads: mayStartThreads,
      }),
    onSuccess: (updated) =>
      qc.setQueryData(queryKey, (current: RepositorySettings[] | undefined) =>
        (current ?? []).map((row) =>
          row.repo === updated.repo ? updated : row
        )
      ),
  })

  const rows = repositories.data ?? []

  return (
    <SettingsSection
      title="Repository permissions"
      description="What each of this workspace's repositories may do on its own. A repository allowed to start threads can do so from a GitHub Actions workflow, using the token the workflow issues itself, with no stored secret. Threads it starts belong to this workspace and to no person."
    >
      {rows.length === 0 ? (
        <p className="px-4 py-3.5 text-xs text-muted-foreground">
          {repositories.isPending
            ? "Loading…"
            : "Bind a repository to this workspace first."}
        </p>
      ) : (
        <ul className="divide-y">
          {rows.map((row) => (
            <li
              key={row.repo}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <span className="font-mono text-xs text-foreground">
                {row.repo}
              </span>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                Can start threads
                <Switch
                  aria-label={`Let ${row.repo} start threads`}
                  checked={row.may_start_threads}
                  onCheckedChange={(on) =>
                    configure.mutate({ repo: row.repo, mayStartThreads: on })
                  }
                  disabled={!canEdit || configure.isPending}
                />
              </label>
            </li>
          ))}
        </ul>
      )}
      {configure.isError && (
        <p className="px-4 pb-3.5 text-xs text-destructive">
          Could not save. Try again.
        </p>
      )}
    </SettingsSection>
  )
}
