import { Link, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { ArrowLeft } from "lucide-react"

import { PlanReview } from "@/components/agents/PlanReview"
import { Skeleton } from "@/components/ui/skeleton"
import { PlanApiError, getPlan } from "@/lib/plan"

export const Route = createFileRoute("/agents/$threadId_/plan")({
  component: PlanPage,
})

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 items-center justify-center p-6">
      {children}
    </div>
  )
}

function BackLink({ threadId }: { threadId: string }) {
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId }}
      className="inline-flex items-center gap-1 text-xs text-[var(--ui-text-dim)] hover:text-[var(--ui-text)]"
    >
      <ArrowLeft className="size-3.5" />
      Back to conversation
    </Link>
  )
}

function PlanPage() {
  const { threadId } = Route.useParams()

  // BlockNote + Yjs are browser-only; mount client-side before rendering.
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const query = useQuery({
    queryKey: ["plan", threadId],
    queryFn: () => getPlan(threadId),
    // The agent shares the link as soon as it enters plan mode, before the plan
    // is written — poll until the plan content is published.
    refetchInterval: (q) => (q.state.data?.markdown ? false : 2000),
    retry: (count, error) =>
      !(
        error instanceof PlanApiError &&
        (error.status === 401 || error.status === 404)
      ) && count < 3,
  })

  if (!mounted || query.isLoading) {
    return (
      <Centered>
        <Skeleton className="h-48 w-full max-w-2xl" />
      </Centered>
    )
  }

  if (query.isError) {
    const status = query.error instanceof PlanApiError ? query.error.status : 0
    return (
      <Centered>
        <div className="space-y-3 text-center text-sm text-[var(--ui-text-dim)]">
          <p>
            {status === 401
              ? "Please sign in to view this plan."
              : "This plan could not be found."}
          </p>
          <BackLink threadId={threadId} />
        </div>
      </Centered>
    )
  }

  const plan = query.data
  if (!plan) {
    return (
      <Centered>
        <Skeleton className="h-48 w-full max-w-2xl" />
      </Centered>
    )
  }
  if (!plan.markdown.trim()) {
    return (
      <Centered>
        <div className="space-y-3 text-center text-sm text-[var(--ui-text-dim)]">
          <p>
            The agent is still writing the plan. This page will update
            automatically…
          </p>
          <BackLink threadId={threadId} />
        </div>
      </Centered>
    )
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="border-b border-[var(--ui-border)] px-6 pt-3">
        <BackLink threadId={threadId} />
      </div>
      <PlanReview plan={plan} />
    </div>
  )
}
