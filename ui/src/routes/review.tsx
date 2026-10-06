import { Link, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { IconWell } from "@langchain/gtm-platform-design-system/ui/icon-well"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import { SettingsPage } from "@/components/AppShell"
import { AlertTriangle, ChevronRight, GitHub } from "@/components/glyphs"
import { api } from "@/lib/api"
import { pageTitle } from "@/lib/pageTitle"
import { useRepos } from "@/lib/profile"

export const Route = createFileRoute("/review")({
  component: ReviewPage,
  head: () => ({ meta: [{ title: pageTitle("Code review") }] }),
})
function ReviewPage() {
  return (
    <SettingsPage
      title="Code review"
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
  const failed = !loading && grouped.length === 0 && repos.isError

  return (
    <PageSection
      title="Repositories"
      description="All installed repositories support on-demand reviews. Click into an installation to configure automatic reviews."
      contained
    >
      {loading ? (
        <Stack className="divide-y divide-line">
          {[0, 1, 2].map((index) => (
            <Inline key={index} gap="md" className="h-row-convo px-4">
              <Skeleton className="size-6 rounded-badge" />
              <Skeleton className="h-3 w-40" />
              <Skeleton className="ml-auto h-3 w-28" />
            </Inline>
          ))}
        </Stack>
      ) : failed ? (
        <Box padding="md">
          <StateNotice
            tone="RISK"
            icon={AlertTriangle}
            title="Could not load your installations"
            description="Nothing changed on your repositories. Try loading them again."
            action={
              <Button
                size="compact"
                variant="outline"
                onClick={() => void repos.refetch()}
              >
                Try again
              </Button>
            }
          />
        </Box>
      ) : grouped.length === 0 ? (
        <EmptyState
          icon={GitHub}
          title="No GitHub App installations found"
          description="Install the Open SWE GitHub App on an account or org to manage repos here."
        />
      ) : (
        <Stack render={<ul />} className="divide-y divide-line">
          {grouped.map(([owner, list]) => {
            const autoReviewCount = list.filter((r) =>
              autoReviewSet.has(r.full_name)
            ).length
            return (
              <li key={owner}>
                <Inline
                  render={
                    <Link to="/review/repositories/$owner" params={{ owner }} />
                  }
                  gap="md"
                  justify="between"
                  className="h-row-convo px-4 hover:bg-hover"
                >
                  <Inline gap="md" className="min-w-0">
                    <IconWell>
                      <Icon icon={GitHub} size="sm" />
                    </IconWell>
                    <Stack gap="none" className="min-w-0">
                      <span className="truncate text-label font-medium text-ink">
                        {owner}
                      </span>
                      <span className="text-meta text-ink-subtle">GitHub</span>
                    </Stack>
                  </Inline>
                  <Inline
                    gap="sm"
                    className="shrink-0 text-meta text-ink-subtle"
                  >
                    <span className="tabular-nums">
                      {autoReviewCount}/{list.length} Run Automatically
                    </span>
                    <Icon icon={ChevronRight} size="sm" />
                  </Inline>
                </Inline>
              </li>
            )
          })}
        </Stack>
      )}
    </PageSection>
  )
}
