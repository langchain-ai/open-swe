import { createFileRoute } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { AccountSection } from "@/features/settings/components/AccountSection"
import { AppShell, SettingsRow, SettingsSection } from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
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
  if (!session.data) {
    if (typeof window === "undefined" || !window.openSweDesktop)
      return <RequireLogin />
    return (
      <main className="mx-auto max-w-3xl space-y-10 px-4 pt-14 pb-16 sm:px-8 sm:py-12">
        <header>
          <h1 className="font-heading text-xl font-medium tracking-tight">
            Settings
          </h1>
          <p className="mt-1.5 text-xs text-muted-foreground">
            Settings for local runs on this computer.
          </p>
        </header>
        <MCPConnectionsSection scope="local" />
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
      <ConnectionsSection user={session.data} />
      <MCPConnectionsSection scope="user" />
      {typeof window !== "undefined" && window.openSweDesktop && (
        <MCPConnectionsSection scope="local" />
      )}
      <PersonalInstructionsSection />
      <DesktopVersionSection />
    </AppShell>
  )
}
