import { Link, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { CaretRightIcon } from "@phosphor-icons/react"
import { useMemo } from "react"
import { IoLogoGithub } from "react-icons/io5"
import {
  AppShell,
  SettingsNavRow,
  SettingsSection,
} from "@/components/AppShell"
import { Empty, EmptyDescription } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { useRepos } from "@/lib/profile"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/review")({
  component: ReviewPage,
  head: () => ({ meta: [{ title: pageTitle("Open SWE Review") }] }),
})
function ReviewPage() {
  const session = useSession()

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
          description="Per-repo review style guides and approval policy overrides."
        />
        <SettingsNavRow
          to="/workspaces"
          label="Workspace review settings"
          description="Guidelines and review toggles are configured on each workspace."
        />
      </SettingsSection>
    </AppShell>
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
          <Empty className="p-4">
            <EmptyDescription>
              No GitHub App installations found. Install the Open SWE GitHub App
              on an account or org to manage repos here.
            </EmptyDescription>
          </Empty>
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
