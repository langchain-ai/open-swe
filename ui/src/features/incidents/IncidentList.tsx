import {
  ArrowRightIcon,
  ClockCounterClockwiseRegularIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import { Link } from "@tanstack/react-router"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useDeferredValue, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { Input } from "@langchain/macaw-components/Input"
import { SirenIcon } from "@phosphor-icons/react/dist/ssr/Siren"

import { invalidationTopic } from "@/lib/invalidations/topics"
import { incidentsApi } from "./api"
import { citationPreview } from "./citations"
import { workspaceApi } from "./workspace-api"
import type { IncidentView } from "./api"
import {
  ErrorState,
  formatTime,
  humanize,
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
    meta: {
      invalidatedBy: [
        invalidationTopic("incidents"),
        invalidationTopic("incident-settings"),
      ],
    },
    retry: false,
  })
  const items = incidents.data?.pages.flatMap((page) => page.items) ?? []
  const filtered = Boolean(search.trim())
  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-8 sm:px-10 sm:py-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight text-primary">
          {view === "history" ? "Incident history" : "Incidents"}
        </h1>
        <Button
          color="secondary"
          variant="plain"
          leftDecorator={ClockCounterClockwiseRegularIcon}
          onClick={() => onViewChange(view === "history" ? "all" : "history")}
        >
          {view === "history" ? "Current incidents" : "Incident history"}
        </Button>
      </div>
      <p className="mt-2 mb-6 text-sm text-secondary">
        Investigations, findings, and the context to act.
      </p>
      <div className="mb-5 flex flex-wrap items-center gap-4">
        {view !== "history" && (
          <div role="group" aria-label="Agent activity filters">
            <GroupedTabs
              size="md"
              value={view}
              onChange={onViewChange}
              options={incidentViews.map(({ value, label }) => ({
                value,
                display: label,
              }))}
            />
          </div>
        )}
        <Input
          size="md"
          role="searchbox"
          aria-label="Search incidents"
          leftIcon={MagnifyingGlassRegularIcon}
          placeholder={
            view === "history"
              ? "Search incident history…"
              : "Search incident titles or channels…"
          }
          value={search}
          onChange={setSearch}
          className="min-w-0 flex-1 basis-56"
        />
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-secondary">
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
      <div className="border-t border-default">
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
          <EmptyState
            className="min-h-96"
            variant="brand"
            icon={SirenIcon}
            title={
              filtered
                ? "No matching incidents"
                : view === "active"
                  ? "Waiting for matching channel events"
                  : view === "all"
                    ? "No incidents yet"
                    : view === "history"
                      ? "No incident history yet"
                      : "No inactive incidents"
            }
            description={
              filtered
                ? "No incidents match your search."
                : view === "active"
                  ? "New public channels matching the prefix appear here once Incidents is enabled."
                  : undefined
            }
          />
        ) : (
          <div className="divide-y divide-default">
            {items.map((item) => (
              <Link
                key={item.id}
                to="/incidents/$incidentId"
                params={{ incidentId: item.id }}
                className="group flex flex-wrap items-start gap-4 px-2 py-5 transition-colors hover:bg-surface-level-1-hover/40"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-medium">
                      {item.title || item.channel_name}
                    </h2>
                    {item.is_archived && (
                      <span className="text-xxs text-secondary">
                        Channel archived
                      </span>
                    )}
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-secondary">
                    {item.latest_finding
                      ? citationPreview(item.latest_finding)
                      : "Gathering incident context. Findings will appear here."}
                  </p>
                  <p className="mt-3 text-xs text-secondary">
                    #{item.channel_name}
                  </p>
                  {item.reason && (
                    <p className="mt-2 text-xs text-warning-secondary">
                      {humanize(item.reason)}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-3 text-xxs text-secondary">
                  <StatusBadge status={item.status} />
                  <time>{formatTime(item.updated_at)}</time>
                  <ArrowRightIcon
                    size={16}
                    weight="regular"
                    className="transition-transform group-hover:translate-x-0.5"
                  />
                </div>
              </Link>
            ))}
          </div>
        )}
        {incidents.hasNextPage && !incidents.error && (
          <div className="border-t border-default p-4 text-center">
            <Button
              color="secondary"
              variant="outlined"
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
