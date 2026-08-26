import { createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { SkillsPage } from "@/features/agents/components/SkillsPage"
import { RequireLogin } from "@/lib/auth-redirect"
import { Skeleton } from "@/components/ui/skeleton"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/skills")({
  component: SkillsSettingsPage,
})

function SkillsSettingsPage() {
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
      title="Skills"
      description="Reusable instructions Open SWE loads when a task matches their description."
    >
      <SkillsPage />
    </AppShell>
  )
}
