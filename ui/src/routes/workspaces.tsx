import { Link, createFileRoute } from "@tanstack/react-router"

import { AuthedAppShell } from "@/components/AppShell"
import { buttonVariants } from "@/components/ui/button"
import { WorkspacesSection } from "@/features/settings/components/WorkspacesSection"

export const Route = createFileRoute("/workspaces")({
  component: WorkspacesPage,
})

function WorkspacesPage() {
  return (
    <AuthedAppShell
      title="Workspaces"
      description="Which repositories and Slack channels each workspace owns, the sandbox image its runs boot from, and how its nightly rebuild went."
    >
      {(user) => (
        <WorkspacesSection
          isAdmin={user.is_admin}
          renderConfigure={(workspace) => (
            <Link
              to="/workspaces/$slug"
              params={{ slug: workspace.slug }}
              aria-label={`Configure ${workspace.name}`}
              className={buttonVariants({ size: "sm", variant: "outline" })}
            >
              Configure
            </Link>
          )}
        />
      )}
    </AuthedAppShell>
  )
}
