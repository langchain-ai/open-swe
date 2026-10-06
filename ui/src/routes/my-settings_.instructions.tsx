import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { PersonalInstructionsSection } from "@/features/settings/components/PersonalInstructionsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/instructions")({
  component: () => (
    <SettingsPage
      title="Instructions"
      description="Standing instructions added to the agent's system prompt for every run you trigger, on any surface. Repository instructions and AGENTS.md win when they conflict."
    >
      <PersonalInstructionsSection />
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("Instructions") }] }),
})
