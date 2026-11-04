import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { SlackIntegrationSection } from "@/features/settings/components/AdminSections"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/integrations")({
  head: () => ({ meta: [{ title: pageTitle("Integrations") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Integrations"
      description="The Slack app shared with every workspace."
    >
      {(user) => (
        <SlackIntegrationSection
          backendUrl={user.slack_base_url ?? user.api_base_url}
        />
      )}
    </SettingsPage>
  ),
})
