import { createFileRoute } from "@tanstack/react-router"
import { IncidentList } from "@/features/incidents/IncidentList"
import type { IncidentView } from "@/features/incidents/api"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/incidents/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { view?: IncidentView } => {
    return {
      view: ["active", "inactive", "all", "history"].includes(
        String(search.view)
      )
        ? (search.view as IncidentView)
        : undefined,
    }
  },
  head: () => ({ meta: [{ title: pageTitle("Incidents") }] }),
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
