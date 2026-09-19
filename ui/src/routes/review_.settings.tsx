import { createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { Skeleton } from "@/components/ui/skeleton"
import {
  ReviewConfiguration,
  type ReviewSettingsTab,
} from "@/features/reviews/components/ReviewConfiguration"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

interface ReviewSettingsSearch {
  tab: ReviewSettingsTab
  repository?: string
}

export const Route = createFileRoute("/review_/settings")({
  validateSearch: (search: Record<string, unknown>): ReviewSettingsSearch => ({
    tab:
      search.tab === "approval" || search.tab === "automation"
        ? search.tab
        : "instructions",
    repository:
      typeof search.repository === "string" ? search.repository : undefined,
  }),
  component: ReviewSettingsPage,
})

function ReviewSettingsPage() {
  const session = useSession()
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  if (session.isLoading)
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  if (!session.data) return <RequireLogin />
  return (
    <AppShell
      user={session.data}
      title="Review settings"
      description="Configure review instructions, shadow approval policy, and automatic review behavior."
      backTo={{ to: "/review", label: "Back to Open SWE Review" }}
    >
      <ReviewConfiguration
        key={search.repository ?? "shared"}
        initialTab={search.tab}
        initialRepository={search.repository ?? null}
        canEdit={session.data.is_admin}
        onTabChange={(tab) =>
          navigate({ search: { ...search, tab }, replace: true })
        }
        onRepositoryChange={(repository) =>
          navigate({
            search: { ...search, repository: repository ?? undefined },
          })
        }
      />
    </AppShell>
  )
}
