import { createFileRoute } from "@tanstack/react-router"

import type {
  AutomationsFilter,
  AutomationsTab,
} from "@/features/automations/components/AutomationsList"
import { AutomationsList } from "@/features/automations/components/AutomationsList"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/automations/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { tab?: AutomationsTab; view?: AutomationsFilter; user?: string } => ({
    tab: search.tab === "runs" ? "runs" : undefined,
    view:
      search.view === "all" || search.view === "system"
        ? search.view
        : undefined,
    user:
      typeof search.user === "string" && search.user ? search.user : undefined,
  }),
  component: AutomationsIndexPage,
  head: () => ({ meta: [{ title: pageTitle("Automations") }] }),
})

function AutomationsIndexPage() {
  const { tab, view, user } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <AutomationsList
      tab={tab ?? "overview"}
      onTabChange={(nextTab) =>
        navigate({
          search: (previous) => ({
            ...previous,
            tab: nextTab === "runs" ? "runs" : undefined,
          }),
          replace: true,
        })
      }
      chosenFilter={view ?? "mine"}
      onFilterChange={(nextView) =>
        navigate({
          search: (previous) => ({
            ...previous,
            view: nextView === "mine" ? undefined : nextView,
            user: undefined,
          }),
          replace: true,
        })
      }
      ownerId={user}
      onOwnerChange={(ownerId) =>
        navigate({
          search: (previous) => ({ ...previous, user: ownerId }),
          replace: true,
        })
      }
    />
  )
}
