import { Link } from "@tanstack/react-router"
import { useInfiniteQuery } from "@tanstack/react-query"
import { useDeferredValue, useState } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { PageMasthead } from "@langchain/gtm-platform-design-system/patterns/page-masthead"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"

import { Activity, AlertTriangle, ArchiveBox, Hash } from "@/components/glyphs"
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

const ROW_CLASS =
  "flex w-full items-start gap-4 px-3 py-3 text-left outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"

function emptyTitle(view: IncidentView, filtered: boolean) {
  if (filtered) return "No matching incidents"
  switch (view) {
    case "active":
      return "Waiting for matching channel events"
    case "all":
      return "No incidents yet"
    case "history":
      return "No incident history yet"
    case "inactive":
      return "No inactive incidents"
  }
}

function viewSummary(view: IncidentView) {
  switch (view) {
    case "inactive":
      return "Paused or completed agent activity"
    case "history":
      return "Retained summaries and postmortems"
    case "active":
      return "Active investigations, including those needing attention"
    case "all":
      return "All current investigations"
  }
}

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
    <ScrollArea overflow="vertical" className="h-full min-h-0 min-w-0 flex-1">
      <Stack
        gap="xl"
        className="mx-auto w-full max-w-work px-6 py-6 max-md:pt-16"
      >
        <PageMasthead
          title={view === "history" ? "Incident history" : "Incidents"}
          description="Investigations, findings, and the context to act."
        />
        <Stack gap="sm">
          <Inline gap="md" wrap>
            {view !== "history" && (
              <ToggleGroup
                aria-label="Agent activity filters"
                value={[view]}
                onValueChange={(value) => {
                  const next = value[0]
                  if (next) onViewChange(next)
                }}
              >
                {incidentViews.map(({ value, label }) => (
                  <ToggleGroupItem key={value} value={value}>
                    {label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            )}
            <Box className="min-w-0 flex-1 basis-56">
              <SearchInput
                size="control"
                label="Search incidents"
                placeholder={
                  view === "history"
                    ? "Search incident history…"
                    : "Search incident titles or channels…"
                }
                value={search}
                onValueChange={setSearch}
              />
            </Box>
            <Button
              variant="ghost"
              onClick={() =>
                onViewChange(view === "history" ? "all" : "history")
              }
            >
              <Icon icon={ArchiveBox} size="sm" />
              {view === "history" ? "Current incidents" : "Incident history"}
            </Button>
          </Inline>
          <Inline
            gap="sm"
            justify="between"
            wrap
            className="text-meta text-ink-subtle"
          >
            <span>{viewSummary(view)}</span>
            <span aria-live="polite">
              {incidents.data
                ? `${items.length} ${items.length === 1 ? "incident" : "incidents"}${incidents.hasNextPage ? " loaded" : ""}`
                : ""}
            </span>
          </Inline>
        </Stack>

        {incidents.isPending ? (
          <LoadingState />
        ) : incidents.error ? (
          <ErrorState
            error={incidents.error}
            retry={() => void incidents.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={Activity}
            title={emptyTitle(view, filtered)}
            description={
              filtered
                ? "No incidents match your search."
                : view === "active"
                  ? "New public channels matching the prefix appear here once Incidents is enabled."
                  : undefined
            }
            className="min-h-96"
          />
        ) : (
          <Stack gap="md">
            <Stack
              render={<ul aria-label="Incidents" />}
              gap="none"
              bg="panel"
              border="line"
              radius="panel"
              className="overflow-hidden"
            >
              {items.map((item) => (
                <Box
                  key={item.id}
                  render={<li />}
                  className="border-b border-line last:border-b-0"
                >
                  <Link
                    to="/incidents/$incidentId"
                    params={{ incidentId: item.id }}
                    className={ROW_CLASS}
                  >
                    <Stack gap="xs" className="min-w-0 flex-1">
                      <Inline gap="sm" wrap>
                        <Box
                          render={<span />}
                          className="text-label font-medium text-ink"
                        >
                          {item.title || item.channel_name}
                        </Box>
                        {item.is_archived && (
                          <Badge tier="plain">Channel archived</Badge>
                        )}
                      </Inline>
                      <Box
                        render={<span />}
                        className="line-clamp-2 text-label text-ink-subtle"
                      >
                        {item.latest_finding
                          ? citationPreview(item.latest_finding)
                          : "Gathering incident context. Findings will appear here."}
                      </Box>
                      <Inline
                        render={<span />}
                        gap="md"
                        wrap
                        className="text-meta text-ink-subtle"
                      >
                        <Inline render={<span />} gap="xs">
                          <Icon icon={Hash} size="sm" />
                          {item.channel_name}
                        </Inline>
                        {item.reason && (
                          <Inline render={<span />} gap="xs" ink="attention">
                            <Icon icon={AlertTriangle} size="sm" />
                            {humanize(item.reason)}
                          </Inline>
                        )}
                      </Inline>
                    </Stack>
                    <Stack gap="xs" align="end" className="shrink-0">
                      <StatusBadge status={item.status} />
                      <Box
                        render={<time />}
                        className="text-meta text-ink-subtle tabular-nums"
                      >
                        {formatTime(item.updated_at)}
                      </Box>
                    </Stack>
                  </Link>
                </Box>
              ))}
            </Stack>
            {incidents.hasNextPage && (
              <Inline justify="center">
                <Button
                  variant="outline"
                  size="compact"
                  disabled={incidents.isFetchingNextPage}
                  onClick={() => void incidents.fetchNextPage()}
                >
                  {incidents.isFetchingNextPage
                    ? "Loading…"
                    : "Load more incidents"}
                </Button>
              </Inline>
            )}
          </Stack>
        )}
      </Stack>
    </ScrollArea>
  )
}
