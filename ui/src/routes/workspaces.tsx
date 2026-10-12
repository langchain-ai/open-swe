import { Link, createFileRoute } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"
import { Skeleton } from "@langchain/macaw-components/Skeleton"

import { AppShell } from "@/components/AppShell"
import { WorkspacesSection } from "@/features/settings/components/WorkspacesSection"
import { RequireLogin } from "@/lib/auth-redirect"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/workspaces")({
  component: WorkspacesPage,
  head: () => ({ meta: [{ title: pageTitle("Workspaces") }] }),
})

function WorkspacesPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-space-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <AppShell
      user={session.data}
      title="Workspaces"
      description="Which repositories and Slack channels each workspace owns, the sandbox image its runs boot from, and how its nightly rebuild went."
    >
      <WorkspacesSection
        isAdmin={session.data.is_admin}
        renderConfigure={(workspace) => (
          <Button
            as={
              <Link to="/workspaces/$slug" params={{ slug: workspace.slug }} />
            }
            aria-label={`Configure ${workspace.name}`}
            size="xs"
            color="secondary"
            variant="outlined"
          >
            Configure
          </Button>
        )}
      />
    </AppShell>
  )
}
