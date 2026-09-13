import { createFileRoute } from "@tanstack/react-router"
import { IncidentList } from "@/features/incidents/IncidentList"
import type { IncidentView } from "@/features/incidents/api"

export const Route = createFileRoute("/incidents/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { view?: IncidentView } => {
    if (search.view === "paused" || search.view === "completed")
      return { view: "inactive" }
    if (search.view === "needs_attention") return { view: "active" }
    return {
      view: ["active", "inactive", "all", "history"].includes(
        String(search.view)
      )
        ? (search.view as IncidentView)
        : undefined,
    }
  },
  component: IncidentsPage,
})

function IncidentsPage() {
  const { view = "active" } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <IncidentList
      view={view}
      onViewChange={(nextView) => {
        void navigate({ search: { view: nextView } })
      }}
    />
  )
}
