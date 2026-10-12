import { CaretRightIcon } from "@langchain/macaw-components/icons"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"
import { useQuery } from "@tanstack/react-query"
import { Link, createFileRoute } from "@tanstack/react-router"
import { useMemo } from "react"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { api } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/review")({
  component: ReviewPage,
  head: () => ({ meta: [{ title: pageTitle("Code review") }] }),
})
function ReviewPage() {
  return (
    <SettingsPage
      title="Code Review"
      description="Open SWE Review checks pull requests for bugs on demand, or automatically on the repositories you choose. Runs are billed by underlying agent usage."
    >
      <RepositoriesSection />
    </SettingsPage>
  )
}

function RepositoriesSection() {
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
      <div className="divide-y divide-default">
        {loading && (
          <div className="p-space-4">
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {!loading && grouped.length === 0 && (
          <p className="px-space-4 py-space-3 text-xs text-secondary">
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
              className="flex items-center justify-between gap-space-4 px-space-4 py-space-3 hover:bg-surface-level-2-hover"
            >
              <div className="flex items-center gap-space-3">
                <GithubLogoIcon
                  weight="regular"
                  className="size-5 shrink-0 text-icon-secondary"
                />
                <div className="flex flex-col gap-0.5">
                  <div className="flex items-center gap-space-2 text-xs">
                    <span className="font-medium text-primary">{owner}</span>
                  </div>
                  <span className="text-xs text-secondary">GitHub</span>
                </div>
              </div>
              <div className="flex items-center gap-space-2 text-xs text-secondary">
                <span>
                  {autoReviewCount}/{list.length} Run Automatically
                </span>
                <CaretRightIcon weight="regular" className="size-3.5" />
              </div>
            </Link>
          )
        })}
      </div>
    </SettingsSection>
  )
}
