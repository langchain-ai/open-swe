import { Link, createFileRoute } from "@tanstack/react-router"

import { AutomationEditor } from "@/features/automations/components/AutomationEditor"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { CalendarXIcon } from "@phosphor-icons/react/dist/ssr/CalendarX"
import { useAgentSchedules } from "@/features/agents/lib/queries"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/automations/$scheduleId")({
  component: EditAutomationPage,
  head: () => ({ meta: [{ title: pageTitle("Edit automation") }] }),
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
    <EmptyState
      className="mx-auto w-full max-w-3xl px-space-5 py-space-9"
      icon={CalendarXIcon}
      title="Automation not found"
      description="This automation could not be found."
      action={
        <Button
          as={<Link to="/agents/automations" />}
          color="secondary"
          variant="outlined"
        >
          Back to Automations
        </Button>
      }
    />
  )
}
