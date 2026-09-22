import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { SettingsSection } from "@/components/AppShell"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"

export function ThreadStartersSection({
  slug,
  repositories,
  canEdit,
}: {
  slug: string
  repositories: string[]
  canEdit: boolean
}) {
  const queryKey = ["threadStarters", slug]
  const qc = useQueryClient()
  const granted = useQuery({
    queryKey,
    queryFn: () => api.getThreadStarters(slug),
  })
  const save = useMutation({
    mutationFn: (repos: string[]) => api.setThreadStarters(slug, repos),
    onSuccess: (result) => qc.setQueryData(queryKey, result),
  })

  const allowed = new Set(
    (save.isPending ? save.variables : granted.data?.repos) ?? []
  )
  const toggle = (repo: string, on: boolean) => {
    const next = new Set(allowed)
    if (on) next.add(repo)
    else next.delete(repo)
    save.mutate([...next])
  }

  return (
    <SettingsSection
      title="Start threads from CI"
      description="A GitHub Actions workflow in one of these repositories can start a thread here using the token it issues itself, with no stored secret. Threads it starts belong to this workspace and to no person."
    >
      {repositories.length === 0 ? (
        <p className="px-4 py-3.5 text-xs text-muted-foreground">
          Bind a repository to this workspace first.
        </p>
      ) : (
        <ul className="divide-y">
          {repositories.map((repo) => (
            <li
              key={repo}
              className="flex items-center justify-between gap-4 px-4 py-3"
            >
              <span className="font-mono text-xs text-foreground">{repo}</span>
              <Switch
                aria-label={`Let ${repo} start threads`}
                checked={allowed.has(repo)}
                onCheckedChange={(on) => toggle(repo, on)}
                disabled={!canEdit || granted.isPending || save.isPending}
              />
            </li>
          ))}
        </ul>
      )}
      {save.isError && (
        <p className="px-4 pb-3.5 text-xs text-destructive">
          Could not save. Try again.
        </p>
      )}
    </SettingsSection>
  )
}
