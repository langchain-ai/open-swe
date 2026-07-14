import { createFileRoute } from "@tanstack/react-router"

import type { AgentSource, AgentStatus } from "@/features/agents/lib/types"
import type { ThreadsPageFilters } from "@/features/agents/components/AgentsThreadsPage";
import { AgentsThreadsPage } from "@/features/agents/components/AgentsThreadsPage"

const SOURCES: ReadonlyArray<AgentSource> = [
  "dashboard",
  "github",
  "slack",
  "linear",
  "schedule",
]
const STATUSES: ReadonlyArray<AgentStatus> = [
  "idle",
  "running",
  "finished",
  "interrupted",
  "error",
]

function parseBool(value: unknown): boolean | undefined {
  if (value === true || value === "true") return true
  if (value === false || value === "false") return false
  return undefined
}

export const Route = createFileRoute("/agents/threads")({
  validateSearch: (search: Record<string, unknown>): ThreadsPageFilters => {
    const source =
      typeof search.source === "string" &&
      SOURCES.includes(search.source as AgentSource)
        ? (search.source as AgentSource)
        : undefined
    const status =
      typeof search.status === "string" &&
      STATUSES.includes(search.status as AgentStatus)
        ? (search.status as AgentStatus)
        : undefined
    const page =
      typeof search.page === "number" && search.page >= 1
        ? Math.floor(search.page)
        : 1
    return {
      resolved: parseBool(search.resolved),
      viewed: parseBool(search.viewed),
      source,
      status,
      q: typeof search.q === "string" && search.q ? search.q : undefined,
      page,
    }
  },
  component: AgentsThreadsRoute,
})

function AgentsThreadsRoute() {
  const filters = Route.useSearch()
  const navigate = Route.useNavigate()

  return (
    <AgentsThreadsPage
      filters={filters}
      onFiltersChange={(next) =>
        navigate({
          search: {
            resolved: next.resolved,
            viewed: next.viewed,
            source: next.source,
            status: next.status,
            q: next.q,
            page: next.page,
          },
        })
      }
    />
  )
}
