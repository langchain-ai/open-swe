import { Link } from "@tanstack/react-router"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useDeferredValue, useState } from "react"
import { ArrowRight, History, Search } from "lucide-react"

import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { incidentsApi } from "./api"
import { citationPreview } from "./citations"
import { workspaceApi } from "./workspace-api"
import type { IncidentView } from "./api"
import {
  ErrorState,
  formatTime,
  humanize,
  IncidentsMark,
  incidentViews,
  LoadingState,
  StatusBadge,
} from "./shared"

export function IncidentList({
  view,
  onViewChange,
}: {
  view: IncidentView
  onViewChange: (view: IncidentView) => void
}) {
  const [search, setSearch] = useState("")
  const q = useDeferredValue(search)
  const incidents = useInfiniteQuery({
    queryKey: ["incidents", "list", view, q],
    queryFn: async ({ pageParam }) => {
      if (view !== "history")
        return incidentsApi.list({ view, q, cursor: pageParam })
      const result = await workspaceApi.history(q, pageParam)
      return {
        ...result,
        items: result.items.map((item) => ({
          ...item,
          channel_id: "",
          is_archived: false,
          reason: null,
          slack_url: null,
          latest_finding: null,
        })),
      }
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 5000,
    retry: false,
  })
  const items = incidents.data?.pages.flatMap((page) => page.items) ?? []
  const filtered = Boolean(search.trim())
  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-10 sm:py-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">
          {view === "history" ? "Incident history" : "Incidents"}
        </h1>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onViewChange(view === "history" ? "all" : "history")}
        >
          <History className="size-3.5" />
          {view === "history" ? "Current incidents" : "Incident history"}
        </Button>
      </div>
      <p className="mt-2 mb-6 text-sm text-muted-foreground">
        Investigations, findings, and the context to act.
      </p>
      <div className="mb-5 flex flex-wrap items-center gap-4">
        {view !== "history" && (
          <ToggleGroup
            aria-label="Agent activity filters"
            spacing={1}
            value={[view]}
            onValueChange={(values) => {
              const next = incidentViews.find(
                (option) => option.value === values[0]
              )
              if (next) onViewChange(next.value)
            }}
            className="rounded-lg border border-border p-1"
          >
            {incidentViews.map(({ value, label }) => (
              <ToggleGroupItem
                key={value}
                value={value}
                className="h-8 px-3 text-muted-foreground aria-pressed:bg-accent aria-pressed:text-foreground"
              >
                {label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        )}
        <div className="relative min-w-0 flex-1 basis-56">
          <Search className="absolute top-2.5 left-3 size-3.5 text-muted-foreground" />
          <Input
            type="search"
            aria-label="Search incidents"
            placeholder={
              view === "history"
                ? "Search incident history…"
                : "Search incident titles or channels…"
            }
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="h-9 bg-transparent pl-9"
          />
        </div>
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {view === "inactive"
            ? "Paused or completed agent activity"
            : view === "history"
              ? "Retained summaries and postmortems"
              : view === "active"
                ? "Active investigations, including those needing attention"
                : "All current investigations"}
        </span>
        <span aria-live="polite">
          {incidents.data
            ? `${items.length} ${items.length === 1 ? "incident" : "incidents"}${incidents.hasNextPage ? " loaded" : ""}`
            : ""}
        </span>
      </div>
      <div className="border-t border-border">
        {incidents.isPending ? (
          <LoadingState />
        ) : incidents.error ? (
          <div className="p-4">
            <ErrorState
              error={incidents.error}
              retry={() => void incidents.refetch()}
            />
          </div>
        ) : items.length === 0 ? (
          <div className="flex min-h-96 flex-col items-center justify-center px-6 py-14 text-center">
            <IncidentsMark className="mb-6 size-14 rounded-2xl" />
            <h2 className="text-base font-medium">
              {filtered
                ? "No matching incidents"
                : view === "active"
                  ? "Waiting for matching channel events"
                  : view === "all"
                    ? "No incidents yet"
                    : view === "history"
                      ? "No incident history yet"
                      : "No inactive incidents"}
            </h2>
            {(filtered || view === "active") && (
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
                {filtered
                  ? "No incidents match your search."
                  : "New public channels matching the prefix appear here once Incidents is enabled."}
              </p>
            )}
          </div>
        ) : (
          <div className="divide-y divide-border">
            {items.map((item) => (
              <Link
                key={item.id}
                to="/incidents/$incidentId"
                params={{ incidentId: item.id }}
                className="group flex flex-wrap items-start gap-4 px-2 py-5 transition-colors hover:bg-accent/40"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-medium">
                      {item.title || item.channel_name}
                    </h2>
                    {item.is_archived && (
                      <span className="text-[11px] text-muted-foreground">
                        Channel archived
                      </span>
                    )}
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                    {item.latest_finding
                      ? citationPreview(item.latest_finding)
                      : "Gathering incident context. Findings will appear here."}
                  </p>
                  <p className="mt-3 text-xs text-muted-foreground">
                    #{item.channel_name}
                  </p>
                  {item.reason && (
                    <p className="mt-2 text-xs text-warning-foreground">
                      {humanize(item.reason)}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-3 text-[11px] text-muted-foreground">
                  <StatusBadge status={item.status} />
                  <time>{formatTime(item.updated_at)}</time>
                  <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
                </div>
              </Link>
            ))}
          </div>
        )}
        {incidents.hasNextPage && !incidents.error && (
          <div className="border-t border-border p-4 text-center">
            <Button
              variant="outline"
              size="sm"
              disabled={incidents.isFetchingNextPage}
              onClick={() => void incidents.fetchNextPage()}
            >
              {incidents.isFetchingNextPage
                ? "Loading…"
                : "Load more incidents"}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
