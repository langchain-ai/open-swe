import { createFileRoute } from "@tanstack/react-router"

import type { AutomationsTab } from "@/features/automations/components/AutomationsList"
import { AutomationsList } from "@/features/automations/components/AutomationsList"

export const Route = createFileRoute("/agents/automations/")({
  validateSearch: (
    search: Record<string, unknown>
  ): { tab?: AutomationsTab } => ({
    tab: search.tab === "runs" ? "runs" : undefined,
  }),
  component: AutomationsIndexPage,
})

function AutomationsIndexPage() {
  const { tab } = Route.useSearch()
  const navigate = Route.useNavigate()
  return (
    <AutomationsList
      tab={tab ?? "overview"}
      onTabChange={(nextTab) =>
        navigate({
          search: { tab: nextTab === "runs" ? "runs" : undefined },
        })
      }
    />
  )
}
