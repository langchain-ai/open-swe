import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { Switch } from "@langchain/macaw-components/Switch"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useMemo, useState } from "react"

import { AppShell } from "@/components/AppShell"
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
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
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
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-medium tracking-wide text-secondary uppercase">
            Repositories
          </h2>
          <span className="text-xs text-secondary">
            {autoReviewCount}/{ownerRepos.length} run automatically
          </span>
        </div>
        <Input
          size="md"
          leftIcon={MagnifyingGlassIcon}
          value={search}
          onChange={(next) => setSearchPosition({ owner, search: next })}
          placeholder="Search repositories…"
          aria-label="Search repositories"
        />
        <div className="rounded-lg border border-default bg-surface-level-1">
          {loading && (
            <div className="p-4">
              <Skeleton className="h-32 w-full" />
            </div>
          )}
          {!loading && filteredRepos.length === 0 && (
            <p className="px-4 py-3 text-xs text-secondary">
              {ownerRepos.length === 0
                ? "No repositories found for this installation."
                : "No repositories match your search."}
            </p>
          )}
          <ul className="divide-y divide-default">
            {pageRepos.map((r) => {
              const runsAutomatically = autoReviewSet.has(r.full_name)
              return (
                <li
                  key={r.full_name}
                  className="flex items-center justify-between gap-4 px-4 py-3"
                >
                  <div className="flex min-w-0 items-center gap-2 text-xs">
                    <span className="truncate">
                      <span className="text-secondary">{owner}/</span>
                      <span className="font-medium text-primary">
                        {r.full_name.slice(owner.length + 1)}
                      </span>
                    </span>
                    {r.private && (
                      <span className="text-xxs text-tertiary">private</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-secondary">
                      Run automatically
                    </span>
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
                        onChange={(v) =>
                          toggleAutoReview.mutate({
                            full_name: r.full_name,
                            on: v,
                          })
                        }
                      />
                    </span>
                  </div>
                </li>
              )
            })}
          </ul>
          {filteredRepos.length > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-4 border-t border-default px-4 py-2 text-xs">
              <span className="text-secondary">
                Showing {pageStart + 1}-{pageEnd} of {filteredRepos.length}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="xs"
                  color="secondary"
                  variant="outlined"
                  disabled={safePage === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  Prev
                </Button>
                <span className="text-secondary">
                  {safePage + 1} / {totalPages}
                </span>
                <Button
                  size="xs"
                  color="secondary"
                  variant="outlined"
                  disabled={safePage >= totalPages - 1}
                  onClick={() =>
                    setPage((p) => Math.min(totalPages - 1, p + 1))
                  }
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>
      </section>
    </AppShell>
  )
}
