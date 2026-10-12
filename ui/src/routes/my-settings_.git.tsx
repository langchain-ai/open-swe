import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { GitSettings } from "@/features/settings/components/GitSettings"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/git")({
  component: () => (
    <SettingsPage
      title="Git"
      description="Repository, branch, and pull request defaults for runs you trigger."
    >
      <GitSettings />
    </SettingsPage>
  ),
  head: () => ({ meta: [{ title: pageTitle("Git") }] }),
})
