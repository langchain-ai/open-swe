import { Link } from "@tanstack/react-router"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useDeferredValue, useState } from "react"
import { ArrowRight, Hash, Search } from "lucide-react"

import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { investigateApi } from "./api"
import type { InvestigationView } from "./api"
import {
  ErrorState,
  formatTime,
  humanize,
  InvestigateMark,
  investigationViews,
  LoadingState,
  StatusBadge,
} from "./shared"

export function InvestigationList({
  view,
  onViewChange,
}: {
  view: InvestigationView
  onViewChange: (view: InvestigationView) => void
}) {
  const [search, setSearch] = useState("")
  const q = useDeferredValue(search)
  const investigations = useInfiniteQuery({
    queryKey: ["investigate", "list", view, q],
    queryFn: ({ pageParam }) =>
      investigateApi.list({
        view,
        q,
        cursor: pageParam,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5000,
    retry: false,
  })
  const items = investigations.data?.pages.flatMap((page) => page.items) ?? []
  const filtered = Boolean(search.trim())
  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-10 sm:py-10">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">
        Investigations
      </h1>
      <div className="rounded-xl border border-border bg-card">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-border p-4">
          <div
            className="flex flex-wrap gap-1"
            aria-label="Investigation status filters"
          >
            {investigationViews.map(({ value, label }) => (
              <button
                type="button"
                key={value}
                onClick={() => onViewChange(value)}
                aria-pressed={view === value}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs transition-colors",
                  view === value
                    ? "bg-accent font-medium text-foreground"
                    : "text-muted-foreground hover:bg-accent/60"
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="ml-auto text-xs text-muted-foreground">
            {investigations.data
              ? `${items.length} ${items.length === 1 ? "investigation" : "investigations"}${investigations.hasNextPage ? " loaded" : ""}`
              : ""}
          </span>
        </div>
        <div className="flex flex-wrap gap-3 border-b border-border p-4">
          <div className="relative min-w-48 flex-1">
            <Search className="absolute top-2.5 left-3 size-3.5 text-muted-foreground" />
            <Input
              type="search"
              aria-label="Search investigations"
              placeholder="Search channels, findings…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="h-9 bg-transparent pl-9"
            />
          </div>
        </div>
        {investigations.isPending ? (
          <LoadingState />
        ) : investigations.error ? (
          <div className="p-4">
            <ErrorState
              error={investigations.error}
              retry={() => void investigations.refetch()}
            />
          </div>
        ) : items.length === 0 ? (
          <div className="flex min-h-96 flex-col items-center justify-center px-6 py-14 text-center">
            <InvestigateMark className="mb-6 size-14 rounded-2xl" />
            <h2 className="text-base font-medium">
              {filtered
                ? "No matching investigations"
                : view === "active"
                  ? "Waiting for matching channel events"
                  : `No ${humanize(view).toLowerCase()} investigations`}
            </h2>
            {(filtered || view === "active") && (
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
                {filtered
                  ? "No channels or findings match your search."
                  : "New public channels matching the prefix appear here once Investigate is enabled."}
              </p>
            )}
          </div>
        ) : (
          <div className="divide-y divide-border">
            {items.map((item) => (
              <Link
                key={item.id}
                to="/investigate/$investigationId"
                params={{ investigationId: item.id }}
                className="group flex items-start gap-4 px-5 py-5 transition-colors hover:bg-accent/40"
              >
                <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  <Hash className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-medium">
                      {item.title || item.channel_name}
                    </h2>
                    <StatusBadge status={item.status} />
                    {item.is_archived && (
                      <span className="text-[11px] text-muted-foreground">
                        Channel archived
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    #{item.channel_name}
                  </p>
                  <p className="mt-3 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                    {item.latest_finding ||
                      "Gathering incident context. Findings will appear here."}
                  </p>
                  {item.reason && (
                    <p className="mt-2 text-xs text-warning-foreground">
                      {humanize(item.reason)}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-3 text-[11px] text-muted-foreground">
                  <time>{formatTime(item.updated_at)}</time>
                  <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
                </div>
              </Link>
            ))}
          </div>
        )}
        {investigations.hasNextPage && !investigations.error && (
          <div className="border-t border-border p-4 text-center">
            <Button
              variant="outline"
              size="sm"
              disabled={investigations.isFetchingNextPage}
              onClick={() => void investigations.fetchNextPage()}
            >
              {investigations.isFetchingNextPage
                ? "Loading…"
                : "Load more investigations"}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
