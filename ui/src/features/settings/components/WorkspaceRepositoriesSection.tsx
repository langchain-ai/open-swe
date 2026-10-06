import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import { Folder } from "@/components/glyphs"
import { api } from "@/lib/api"
import type { RepositorySettings } from "@/lib/api"
import { optimisticUpdate, usePendingVariables } from "@/lib/optimistic"

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
  const configureKey = ["workspaceRepositories", slug, "configure"]
  const pendingRepos = usePendingVariables<{ repo: string }>(configureKey)
  const configure = useMutation({
    mutationKey: configureKey,
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
    meta: { errorTitle: "Couldn't update repository permissions" },
    onMutate: async ({ repo, mayStartThreads }) => ({
      undo: await optimisticUpdate<RepositorySettings[]>(
        qc,
        queryKey,
        (current) =>
          current.map((row) =>
            row.repo === repo
              ? { ...row, may_start_threads: mayStartThreads }
              : row
          )
      ),
    }),
    onError: (_error, _vars, context) => context?.undo(),
    onSuccess: (updated) =>
      qc.setQueryData(queryKey, (current: RepositorySettings[] | undefined) =>
        (current ?? []).map((row) =>
          row.repo === updated.repo ? updated : row
        )
      ),
  })

  const rows = repositories.data ?? []

  return (
    <SettingSection
      contained
      title="Repository permissions"
      description="What each of this workspace's repositories may do on its own. A repository allowed to start threads can do so from a GitHub Actions workflow, using the token the workflow issues itself, with no stored secret. Threads it starts belong to this workspace and to no person."
    >
      {rows.length === 0 ? (
        repositories.isPending ? (
          <Box padding="lg">
            <Skeleton className="h-row-data w-full" />
          </Box>
        ) : (
          <EmptyState
            icon={Folder}
            title="No bound repositories"
            description="Bind a repository to this workspace first."
          />
        )
      ) : (
        rows.map((row) => (
          <SettingRow
            key={row.repo}
            label={row.repo}
            description="Can start threads"
            density="compact"
            control={(slot) => (
              <Switch
                id={slot.id}
                aria-describedby={slot.describedById}
                aria-label={`Let ${row.repo} start threads`}
                checked={row.may_start_threads}
                onCheckedChange={(on) =>
                  configure.mutate({ repo: row.repo, mayStartThreads: on })
                }
                disabled={
                  !canEdit ||
                  pendingRepos.some((vars) => vars.repo === row.repo)
                }
              />
            )}
          />
        ))
      )}
    </SettingSection>
  )
}
