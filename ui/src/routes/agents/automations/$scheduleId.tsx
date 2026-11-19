import { Link, createFileRoute } from "@tanstack/react-router"

import { AutomationEditor } from "@/features/automations/components/AutomationEditor"
import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { Skeleton } from "@/components/Skeleton"
import { CalendarXIcon } from "@phosphor-icons/react/dist/ssr/CalendarX"
import { useAgentSchedules } from "@/features/agents/lib/queries"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents/automations/$scheduleId")({
  component: EditAutomationPage,
  head: () => ({ meta: [{ title: pageTitle("Edit automation") }] }),
})

function EditAutomationPage() {
  const { scheduleId } = Route.useParams()
  // Admins open any user's automation, not only those their default list shows.
  const isAdmin = useSession().data?.is_admin === true
  const schedulesQuery = useAgentSchedules({ allUsers: isAdmin })
  const schedule = schedulesQuery.data?.find((s) => s.id === scheduleId)

  if (schedulesQuery.isLoading) {
    return (
      <div
        role="status"
        aria-label="Loading automation"
        className="flex min-w-0 flex-1 flex-col"
      >
        <div className="flex items-center justify-between gap-space-3 px-space-5 py-space-4 max-md:pt-space-8">
          <Skeleton className="h-3 w-40" />
          <div className="flex items-center gap-space-2">
            <Skeleton className="h-9 w-24 rounded-md" />
            <Skeleton className="h-9 w-28 rounded-md" />
          </div>
        </div>
        <div className="mx-auto w-full max-w-3xl space-y-space-3 px-space-5 pt-space-2">
          <Skeleton className="h-10 w-full rounded-md" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="mt-space-3 h-32 w-full rounded-xl" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
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
