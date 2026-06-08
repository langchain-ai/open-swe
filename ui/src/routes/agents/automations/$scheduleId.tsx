import { Link, createFileRoute } from "@tanstack/react-router"

import { AutomationEditor } from "@/components/agents/AutomationEditor"
import { Skeleton } from "@/components/ui/skeleton"
import { useAgentSchedules } from "@/lib/agents/queries"

export const Route = createFileRoute("/agents/automations/$scheduleId")({
  component: EditAutomationPage,
})

function EditAutomationPage() {
  const { scheduleId } = Route.useParams()
  const schedulesQuery = useAgentSchedules()
  const schedule = schedulesQuery.data?.find((s) => s.id === scheduleId)

  if (schedulesQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-6 py-10">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="mt-6 h-32 w-full" />
      </div>
    )
  }

  if (schedule) {
    return <AutomationEditor mode="edit" schedule={schedule} />
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-16 text-center">
      <p className="text-sm text-[var(--ui-text-muted)]">
        This automation could not be found.
      </p>
      <Link
        to="/agents/automations"
        className="mt-3 inline-block text-sm text-[var(--ui-accent)] hover:underline"
      >
        Back to Automations
      </Link>
    </div>
  )
}
