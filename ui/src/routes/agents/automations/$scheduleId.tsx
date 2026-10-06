import { Link, createFileRoute } from "@tanstack/react-router"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"

import { Zap } from "@/components/glyphs"
import { AutomationEditor } from "@/features/automations/components/AutomationEditor"
import { useAgentSchedules } from "@/features/agents/lib/queries"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/automations/$scheduleId")({
  component: EditAutomationPage,
  head: () => ({ meta: [{ title: pageTitle("Edit automation") }] }),
})

const PAGE_CLASS = "mx-auto w-full max-w-reading px-6 py-6 max-md:pt-16"

function EditAutomationPage() {
  const { scheduleId } = Route.useParams()
  const schedulesQuery = useAgentSchedules()
  const schedule = schedulesQuery.data?.find((s) => s.id === scheduleId)

  if (schedulesQuery.isLoading) {
    return (
      <Stack gap="xl" aria-busy className={PAGE_CLASS}>
        <Skeleton className="h-control w-64" />
        <Skeleton className="h-32 w-full rounded-panel" />
      </Stack>
    )
  }

  if (schedule) {
    return <AutomationEditor mode="edit" schedule={schedule} />
  }

  return (
    <Stack className={PAGE_CLASS}>
      <EmptyState
        icon={Zap}
        title="This automation could not be found"
        description="It may have been deleted."
        action={
          <Link
            to="/agents/automations"
            className={buttonVariants({ variant: "outline" })}
          >
            Back to Automations
          </Link>
        }
      />
    </Stack>
  )
}
