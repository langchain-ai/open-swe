import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { ArrowLeft } from "lucide-react"
import { useIsHydrated } from "@/lib/hydration"

import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { PlanReview } from "@/features/agents/components/PlanReview"
import { buttonVariants } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { loginUrl } from "@/lib/api"
import { currentAuthRedirectPath } from "@/lib/auth-redirect"
import { PlanApiError, getPlan } from "@/lib/plan"

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center px-4 py-6">
      {children}
    </div>
  )
}

function BackLink({ threadId }: { threadId: string }) {
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId }}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground/70 hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" />
      Back to conversation
    </Link>
  )
}

export function planSignInHref(): string {
  return loginUrl(currentAuthRedirectPath())
}

export function PlanSignInButton() {
  return (
    <a href={planSignInHref()} className={buttonVariants({ size: "sm" })}>
      Sign in to view this plan
    </a>
  )
}

export function PlanView({
  threadId,
  standalone = false,
  onApprove,
}: {
  threadId: string
  standalone?: boolean
  onApprove?: (runId: string) => void
}) {
  const mounted = useIsHydrated()

  const query = useQuery({
    queryKey: ["plan", threadId],
    queryFn: () => getPlan(threadId),
    refetchInterval: (q) =>
      q.state.data?.html || q.state.data?.markdown ? false : 2000,
    retry: (count, error) =>
      !(
        error instanceof PlanApiError &&
        (error.status === 401 || error.status === 404)
      ) && count < 3,
  })
  const backLink = standalone ? <BackLink threadId={threadId} /> : null

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
        <div className="space-y-3 text-center text-sm text-muted-foreground/70">
          <p>
            {status === 401
              ? "Please sign in to view this plan."
              : "This plan could not be found."}
          </p>
          {status === 401 ? <PlanSignInButton /> : null}
          {backLink}
        </div>
      </Centered>
    )
  }

  const plan = query.data
  if (!plan?.html.trim() && !plan?.markdown.trim()) {
    return (
      <Centered>
        <div className="space-y-3 text-center text-sm text-muted-foreground/70">
          <p>
            The agent is still writing the content. This view will update
            automatically…
          </p>
          {backLink}
        </div>
      </Centered>
    )
  }

  if (standalone && plan.html.trim()) {
    return (
      <div className="fixed inset-0 z-50 flex min-h-0 min-w-0 flex-col bg-background">
        <nav className="flex h-9 shrink-0 items-center border-b border-border px-3">
          {backLink}
        </nav>
        <PlanArtifactFrame
          html={plan.html}
          className="min-h-0 min-w-0 flex-1"
        />
      </div>
    )
  }

  return (
    <div
      className={
        standalone
          ? "fixed inset-0 z-50 flex min-h-0 min-w-0 flex-col bg-background"
          : "flex min-h-0 min-w-0 flex-1 flex-col"
      }
    >
      {standalone && (
        <nav className="flex h-9 shrink-0 items-center border-b border-border px-3">
          {backLink}
        </nav>
      )}
      <PlanReview plan={plan} onApprove={onApprove} />
    </div>
  )
}
