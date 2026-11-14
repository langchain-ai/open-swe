import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { SlackIntegrationSection } from "@/features/settings/components/AdminSections"
import { BotToolsSection } from "@/features/settings/components/ManagedToolsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/integrations")({
  head: () => ({ meta: [{ title: pageTitle("Integrations") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Integrations"
      description="The Slack app and the bot's managed tools, shared with every workspace."
    >
      {(user) => (
        <>
          <SlackIntegrationSection
            backendUrl={user.slack_base_url ?? user.api_base_url}
          />
          <BotToolsSection />
        </>
      )}
    </SettingsPage>
  ),
})
