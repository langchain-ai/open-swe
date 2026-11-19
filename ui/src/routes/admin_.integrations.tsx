import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { AllowedSlackBotsSection } from "@/features/settings/components/AllowedSlackBotsSection"
import { SlackIntegrationSection } from "@/features/settings/components/AdminSections"
import { RoutingSettings } from "@/features/settings/components/RoutingSettings"
import { BotToolsSection } from "@/features/settings/components/ManagedToolsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/integrations")({
  head: () => ({ meta: [{ title: pageTitle("Integrations") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Integrations"
      description="Inbound routing, Slack channels, and Robin's shared managed tools."
    >
      {(user) => (
        <>
          <SlackIntegrationSection
            backendUrl={user.slack_base_url ?? user.api_base_url}
          >
            <AllowedSlackBotsSection />
          </SlackIntegrationSection>
          <RoutingSettings />
          <BotToolsSection />
        </>
      )}
    </SettingsPage>
  ),
})
