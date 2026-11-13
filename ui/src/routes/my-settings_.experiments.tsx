import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage, SettingsSection } from "@/components/AppShell"
import { StreamPreference } from "@/features/settings/components/StreamPreference"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/experiments")({
  component: ExperimentsPage,
  head: () => ({ meta: [{ title: pageTitle("Experiments") }] }),
})

function ExperimentsPage() {
  return (
    <SettingsPage
      title="Experiments"
      description="Features under test. They may change or go away."
    >
      <SettingsSection title="Interface">
        <StreamPreference />
      </SettingsSection>
      <SettingsSection title="Slack">
        <ProfileSwitchRow
          field="concierge_mode"
          label="Concierge mode"
          description="Your whole DM with Robin becomes one private thread it always answers in, instead of a new thread per message."
        />
      </SettingsSection>
      <SettingsSection title="Agent">
        <ProfileSwitchRow
          field="experimental_classify"
          label="Classify tool"
          description="Robin can classify text or JSON from code mode with TypeSafe's System One model: fast, typed decisions with calibrated confidence."
        />
        <ProfileSwitchRow
          field="experimental_force_code_mode"
          label="Force code mode"
          description="In threads you create, call every code-compatible tool through eval, including file, shell, and search tools. Synchronous subagents are excluded. Replies, browser, and state-changing controls stay direct. Off by default."
        />
        <ProfileSwitchRow
          field="experimental_local_auto_mode"
          label="Auto mode on This Mac"
          description="In threads that run on your Mac, a classifier reviews each command and file change before it runs. Routine work inside the project goes ahead; anything that touches credentials, sends data away, deletes outside the project or sets up background jobs is blocked, and Robin carries on without it."
        />
        <ProfileSwitchRow
          field="experimental_background_callbacks"
          label="Background command callbacks"
          description="Background commands you start report completion from the sandbox instead of being polled every minute."
        />
      </SettingsSection>
    </SettingsPage>
  )
}
