import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import {
  ClaudeCodeAboutSection,
  ClaudeCodeMachinesSection,
  ClaudeCodeSetupSection,
} from "@/features/settings/components/ClaudeCodeSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/claude-code")({
  component: ClaudeCodePage,
  head: () => ({ meta: [{ title: pageTitle("Claude Code") }] }),
})

function ClaudeCodePage() {
  return (
    <SettingsPage
      title="Claude Code"
      description="Follow your Claude Code sessions here as private, read-only threads that update while Claude works."
    >
      <ClaudeCodeSetupSection />
      <ClaudeCodeMachinesSection />
      <ClaudeCodeAboutSection />
    </SettingsPage>
  )
}
