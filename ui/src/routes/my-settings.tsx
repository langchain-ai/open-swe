import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { AccountSection } from "@/features/settings/components/AccountSection"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { PersonalInstructionsSection } from "@/features/settings/components/PersonalInstructionsSection"
import { PreferencesSection } from "@/features/settings/components/PreferencesSection"
import { PullRequestsSection } from "@/features/settings/components/PullRequestsSection"
import { RequireLogin } from "@/lib/auth-redirect"
import { Skeleton } from "@/components/ui/skeleton"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/my-settings")({
  component: MySettingsPage,
})

function DesktopVersionSection() {
  const [version, setVersion] = useState<string>()

  useEffect(() => {
    void window.openSweDesktop?.getVersion().then(setVersion)
  }, [])

  if (!version) return null
  return (
    <SettingsSection title="About">
      <SettingsRow
        label="Open SWE Desktop"
        control={
          <span className="text-xs text-muted-foreground">
            Version {version}
          </span>
        }
      />
    </SettingsSection>
  )
}

function MySettingsPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <AppShell
      user={session.data}
      title="Settings"
      description="Personal preferences, connected accounts, and instructions that apply to every run you trigger."
    >
      <AccountSection user={session.data} />
      <PreferencesSection />
      <PullRequestsSection />
      <ConnectionsSection user={session.data} />
      <PersonalInstructionsSection />
      <DesktopVersionSection />
    </AppShell>
  )
}
