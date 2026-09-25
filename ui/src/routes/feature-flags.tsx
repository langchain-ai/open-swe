import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AppShell, SettingsSection } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { AssistantUiPreference } from "@/features/settings/components/AssistantUiPreference"
import { BackgroundCallbacksPreference } from "@/features/settings/components/BackgroundCallbacksPreference"
import { RequireLogin } from "@/lib/auth-redirect"
import { useFeatureFlagsPanel } from "@/lib/featureFlags"
import { useShortcutLabel } from "@/lib/hotkeys"
import { useIsHydrated } from "@/lib/hydration"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/feature-flags")({
  component: FeatureFlagsPage,
  head: () => ({ meta: [{ title: pageTitle("Feature Flags") }] }),
})

function FeatureFlagsPage() {
  const session = useSession()
  const visible = useFeatureFlagsPanel()
  const hydrated = useIsHydrated()
  const paletteShortcut = useShortcutLabel("mod+k")

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />
  if (hydrated && !visible) return <Navigate to="/my-settings" replace />

  return (
    <AppShell
      user={session.data}
      title="Feature Flags"
      description={`Experimental features under test. Toggle this tab from the command palette (${paletteShortcut}).`}
    >
      <SettingsSection title="Experiments">
        <AssistantUiPreference />
        <BackgroundCallbacksPreference />
      </SettingsSection>
    </AppShell>
  )
}
