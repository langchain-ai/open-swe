import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { AccountSection } from "@/features/settings/components/AccountSection"
import { ApiKeysSection } from "@/features/settings/components/ApiKeysSection"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { EnvironmentsSection } from "@/features/settings/components/EnvironmentsSection"
import { PersonalInstructionsSection } from "@/features/settings/components/PersonalInstructionsSection"
import { PreferencesSection } from "@/features/settings/components/PreferencesSection"
import { PullRequestsSection } from "@/features/settings/components/PullRequestsSection"
import { RequireLogin } from "@/lib/auth-redirect"
import { Skeleton } from "@/components/ui/skeleton"
import { isDesktopLocalModeEnabled } from "@/lib/desktop-local-mode"
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
  if (!session.data) {
    if (!isDesktopLocalModeEnabled()) return <RequireLogin />
    return (
      <main className="mx-auto max-w-3xl space-y-10 px-4 pt-14 pb-16 sm:px-8 sm:py-12">
        <ApiKeysSection />
        <DesktopVersionSection />
      </main>
    )
  }

  return (
    <AppShell
      user={session.data}
      title="Settings"
      description="Personal preferences, connected accounts, and instructions that apply to every run you trigger."
    >
      <AccountSection user={session.data} />
      <PreferencesSection />
      <PullRequestsSection />
      <EnvironmentsSection isAdmin={session.data.is_admin} />
      <ConnectionsSection user={session.data} />
      <PersonalInstructionsSection />
      <ApiKeysSection />
      <DesktopVersionSection />
    </AppShell>
  )
}
