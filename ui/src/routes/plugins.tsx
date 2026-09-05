import { createFileRoute, Link } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import { SkillsPage } from "@/features/agents/components/SkillsPage"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { McpConnectionsSection } from "@/features/settings/components/McpConnectionsSection"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/plugins")({
  validateSearch: (
    search: Record<string, unknown>
  ): { tab: "mcps" | "skills" } => ({
    tab: search.tab === "skills" ? "skills" : "mcps",
  }),
  component: PluginsPage,
})

function PluginsPage() {
  const session = useSession()
  const { tab } = Route.useSearch()
  if (session.isLoading)
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  if (!session.data) return <RequireLogin />

  return (
    <AppShell
      user={session.data}
      title="Plugins"
      description="Extend Open SWE with your tools and reusable skills."
      className="max-w-4xl"
    >
      <nav
        aria-label="Plugin categories"
        className="flex gap-6 border-b border-border"
      >
        {(["mcps", "skills"] as const).map((value) => (
          <Link
            key={value}
            to="/plugins"
            search={{ tab: value }}
            aria-current={tab === value ? "page" : undefined}
            className={cn(
              "border-b-2 px-1 pb-3 text-sm transition-colors",
              tab === value
                ? "border-foreground font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {value === "mcps" ? "MCPs" : "Skills"}
          </Link>
        ))}
      </nav>
      {tab === "skills" ? (
        <SkillsPage />
      ) : (
        <>
          <McpConnectionsSection
            key={session.data.login}
            login={session.data.login}
          />
          <ConnectionsSection user={session.data} />
        </>
      )}
    </AppShell>
  )
}
