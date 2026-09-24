import { createFileRoute } from "@tanstack/react-router"

import { AppShell, SettingsSection } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { AssistantUiPreference } from "@/features/settings/components/AssistantUiPreference"
import { RequireLogin } from "@/lib/auth-redirect"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/feature-flags")({
  component: FeatureFlagsPage,
  head: () => ({ meta: [{ title: pageTitle("Feature Flags") }] }),
})

function FeatureFlagsPage() {
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
      title="Feature Flags"
      description="Experimental features under test. Toggle this tab with Ctrl+Shift+E (⌘+Shift+E on Mac)."
    >
      <SettingsSection title="Experiments">
        <AssistantUiPreference />
      </SettingsSection>
    </AppShell>
  )
}
