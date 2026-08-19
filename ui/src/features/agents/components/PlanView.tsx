import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { ArrowLeft } from "lucide-react"

import { PlanReview } from "@/features/agents/components/PlanReview"
import { buttonVariants } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { loginUrl } from "@/lib/api"
import { currentAuthRedirectPath } from "@/lib/auth-redirect"
import { PlanApiError, getPlan } from "@/lib/plan"
import { cn } from "@/lib/utils"

function Centered({
  children,
  standalone,
}: {
  children: React.ReactNode
  standalone: boolean
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 items-center justify-center px-4 py-6",
        standalone && "max-md:pt-14 md:p-6"
      )}
    >
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
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

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
      <Centered standalone={standalone}>
        <Skeleton className="h-48 w-full max-w-2xl" />
      </Centered>
    )
  }

  if (query.isError) {
    const status = query.error instanceof PlanApiError ? query.error.status : 0
    return (
      <Centered standalone={standalone}>
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
      <Centered standalone={standalone}>
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

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {standalone && (
        <div className="border-b border-border px-4 pt-14 md:px-6 md:pt-3">
          {backLink}
        </div>
      )}
      <PlanReview plan={plan} onApprove={onApprove} />
    </div>
  )
}
