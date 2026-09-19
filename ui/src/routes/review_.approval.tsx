import { createFileRoute } from "@tanstack/react-router"

import { AppShell } from "@/components/AppShell"
import { ApprovalPolicyPanel } from "@/features/reviews/components/ApprovalPolicyPanel"
import { Skeleton } from "@/components/ui/skeleton"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/review_/approval")({
  component: ReviewApprovalPage,
})

function ReviewApprovalPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <RequireLogin />

  return (
    <AppShell
      user={session.data}
      title="Approval Policy"
      description="Set the shared policy and repository overrides used for shadow approval evaluation."
      backTo={{ to: "/review", label: "Back to Open SWE Review" }}
    >
      <div className="rounded-lg border border-border bg-card">
        <ApprovalPolicyPanel />
      </div>
    </AppShell>
  )
}
