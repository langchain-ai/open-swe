import { Link, createFileRoute } from "@tanstack/react-router"

import { buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { SettingsPage } from "@/components/AppShell"
import { WorkspacesSection } from "@/features/settings/components/WorkspacesSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/workspaces")({
  component: WorkspacesPage,
  head: () => ({ meta: [{ title: pageTitle("Workspaces") }] }),
})

function WorkspacesPage() {
  return (
    <SettingsPage
      title="Workspaces"
      description="Which repositories and Slack channels each workspace owns, the sandbox image its runs boot from, and how its nightly rebuild went."
      contentWidth="work"
    >
      {(user) => (
        <WorkspacesSection
          isAdmin={user.is_admin}
          renderConfigure={(workspace) => (
            <Link
              to="/workspaces/$slug"
              params={{ slug: workspace.slug }}
              aria-label={`Configure ${workspace.name}`}
              className={buttonVariants({
                size: "compact",
                variant: "outline",
              })}
            >
              Configure
            </Link>
          )}
        />
      )}
    </SettingsPage>
  )
}
