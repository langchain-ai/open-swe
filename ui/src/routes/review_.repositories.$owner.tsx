import { createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  SettingRow,
  SettingSection,
} from "@langchain/gtm-platform-design-system/patterns/setting-section"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"

import { AppShell } from "@/components/AppShell"
import { ChevronLeft, ChevronRight, GitHub, Search } from "@/components/glyphs"
import { api } from "@/lib/api"
import { RequireLogin } from "@/lib/auth-redirect"
import { pageTitle } from "@/lib/pageTitle"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"

const PAGE_SIZE = 20
const autoReviewMutationKey = ["setAutoReviewRepo"]

type AutoReviewRepos = Awaited<ReturnType<typeof api.listAutoReviewRepos>>

export const Route = createFileRoute("/review_/repositories/$owner")({
  component: RepositoriesOwnerPage,
  head: ({ params }: { params: { owner: string } }) => ({
    meta: [{ title: pageTitle(`${params.owner} repositories`) }],
  }),
})

function RepositoriesOwnerPage() {
  const session = useSession()
  const { owner } = Route.useParams()
  const qc = useQueryClient()

  const repos = useRepos()

  const autoReview = useQuery({
    queryKey: ["autoReviewRepos"],
    queryFn: api.listAutoReviewRepos,
    enabled: !!session.data,
  })

  const [toggling, setToggling] = useState<ReadonlySet<string>>(new Set())
  const setRepoAutoReview = (full_name: string, on: boolean) =>
    qc.setQueryData<AutoReviewRepos>(["autoReviewRepos"], (old) => {
      const rest = (old?.repos ?? []).filter((name) => name !== full_name)
      return { repos: on ? [...rest, full_name] : rest }
    })
  const toggleAutoReview = useMutation({
    mutationKey: autoReviewMutationKey,
    mutationFn: ({ full_name, on }: { full_name: string; on: boolean }) =>
      api.setAutoReviewRepo(full_name, on),
    meta: { errorTitle: "Couldn't update auto-review" },
    onMutate: async ({ full_name, on }) => {
      setToggling((prev) => new Set(prev).add(full_name))
      await qc.cancelQueries({ queryKey: ["autoReviewRepos"] })
      setRepoAutoReview(full_name, on)
    },
    onSuccess: (data) => {
      if (qc.isMutating({ mutationKey: autoReviewMutationKey }) === 1)
        qc.setQueryData(["autoReviewRepos"], data)
    },
    onError: (_error, { full_name, on }) => setRepoAutoReview(full_name, !on),
    onSettled: (_data, _error, { full_name }) => {
      setToggling((prev) => {
        const next = new Set(prev)
        next.delete(full_name)
        return next
      })
      if (qc.isMutating({ mutationKey: autoReviewMutationKey }) === 1)
        void qc.invalidateQueries({ queryKey: ["autoReviewRepos"] })
    },
  })

  const ownerRepos = useMemo(
    () =>
      (repos.data?.repositories ?? [])
        .filter((r) => r.full_name.split("/")[0] === owner)
        .sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [repos.data?.repositories, owner]
  )

  const autoReviewSet = useMemo(
    () => new Set(autoReview.data?.repos ?? []),
    [autoReview.data?.repos]
  )

  const [searchPosition, setSearchPosition] = useState({ owner, search: "" })
  const search = searchPosition.owner === owner ? searchPosition.search : ""
  const query = search.trim().toLowerCase()
  const filteredRepos = query
    ? ownerRepos.filter((r) => r.full_name.toLowerCase().includes(query))
    : ownerRepos

  const pageKey = `${owner}:${query}`
  const [pagePosition, setPagePosition] = useState({ key: pageKey, page: 0 })
  const page = pagePosition.key === pageKey ? pagePosition.page : 0
  const setPage = (next: (current: number) => number) => {
    setPagePosition({ key: pageKey, page: next(page) })
  }

  const totalPages = Math.max(1, Math.ceil(filteredRepos.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages - 1)
  const pageStart = safePage * PAGE_SIZE
  const pageEnd = Math.min(pageStart + PAGE_SIZE, filteredRepos.length)
  const pageRepos = filteredRepos.slice(pageStart, pageEnd)

  if (session.isLoading) {
    return (
      <Box render={<main />} padding="xl">
        <Skeleton className="h-64 w-full rounded-panel" />
      </Box>
    )
  }
  if (!session.data) return <RequireLogin />

  const canEdit = session.data.is_admin
  const autoReviewCount = ownerRepos.filter((r) =>
    autoReviewSet.has(r.full_name)
  ).length
  const loading = repos.isLoading || autoReview.isLoading

  return (
    <AppShell
      user={session.data}
      title={owner}
      description={
        canEdit
          ? "Choose which repositories run Open SWE Review automatically. All installed repositories remain available for on-demand reviews."
          : "Automatic review settings are read-only for non-admins."
      }
      backTo={{ to: "/review", label: "Back to Code review" }}
    >
      <SettingSection
        title="Repositories"
        actions={
          <span className="text-meta text-ink-subtle tabular-nums">
            {autoReviewCount}/{ownerRepos.length} run automatically
          </span>
        }
        contained
      >
        <Box className="pb-2">
          <SearchInput
            size="control"
            value={search}
            onValueChange={(value) =>
              setSearchPosition({ owner, search: value })
            }
            placeholder="Search repositories…"
            label="Search repositories"
          />
        </Box>
        {loading ? (
          <Stack gap="md" className="px-5 py-3">
            {[0, 1, 2, 3].map((index) => (
              <Inline key={index} gap="md" justify="between">
                <Skeleton className="h-3 w-48" />
                <Skeleton className="h-5 w-9 rounded-full" />
              </Inline>
            ))}
          </Stack>
        ) : filteredRepos.length === 0 ? (
          ownerRepos.length === 0 ? (
            <EmptyState
              icon={GitHub}
              title="No repositories found for this installation."
            />
          ) : (
            <EmptyState
              icon={Search}
              title="No repositories match your search."
              action={
                <Button
                  size="compact"
                  variant="outline"
                  onClick={() => setSearchPosition({ owner, search: "" })}
                >
                  Clear search
                </Button>
              }
            />
          )
        ) : (
          pageRepos.map((r) => {
            const runsAutomatically = autoReviewSet.has(r.full_name)
            return (
              <SettingRow
                key={r.full_name}
                density="compact"
                label={r.full_name}
                badge={
                  r.private ? (
                    <Badge tier="notable" tone="neutral">
                      private
                    </Badge>
                  ) : undefined
                }
                control={() => (
                  <span
                    title={
                      !canEdit
                        ? "Only team admins can modify automatic review settings"
                        : undefined
                    }
                    className={!canEdit ? "cursor-not-allowed" : undefined}
                  >
                    <Switch
                      aria-label={`Run reviews automatically for ${r.full_name}`}
                      checked={runsAutomatically}
                      disabled={!canEdit || toggling.has(r.full_name)}
                      onCheckedChange={(v) =>
                        toggleAutoReview.mutate({
                          full_name: r.full_name,
                          on: v,
                        })
                      }
                    />
                  </span>
                )}
              />
            )
          })
        )}
        {filteredRepos.length > PAGE_SIZE && (
          <Inline
            gap="lg"
            justify="between"
            className="mt-2 border-t border-line px-5 pt-3 text-label"
          >
            <span className="text-ink-subtle tabular-nums">
              Showing {pageStart + 1}-{pageEnd} of {filteredRepos.length}
            </span>
            <Inline gap="sm">
              <Button
                size="compact"
                variant="outline"
                disabled={safePage === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                <Icon icon={ChevronLeft} size="sm" />
                Prev
              </Button>
              <span className="text-ink-subtle tabular-nums">
                {safePage + 1} / {totalPages}
              </span>
              <Button
                size="compact"
                variant="outline"
                disabled={safePage >= totalPages - 1}
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              >
                Next
                <Icon icon={ChevronRight} size="sm" />
              </Button>
            </Inline>
          </Inline>
        )}
      </SettingSection>
    </AppShell>
  )
}
