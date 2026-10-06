import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { useIsHydrated } from "@/lib/hydration"

import { PlanReview } from "@/features/agents/components/PlanReview"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { Box, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { buttonVariants } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Spinner } from "@langchain/gtm-platform-design-system/ui/spinner"
import { ArrowLeft, FileSearch, Lock } from "@/components/glyphs"
import { loginUrl } from "@/lib/api"
import { currentAuthRedirectPath } from "@/lib/auth-redirect"
import { PlanApiError, getPlan } from "@/lib/plan"
import { pageTitle } from "@/lib/pageTitle"
import { cn } from "@/lib/utils"

const DEFAULT_TITLE = pageTitle("Artifact")

/** Publishes the artifact's own `<title>` as the document title. */
function artifactTitle(html: string, markdown: string): string | null {
  const source = html.trim() ? html : markdown
  const found = /<title\b[^>]*>([\s\S]*?)<\/title\s*>/i.exec(source)
  const raw = found
    ?.at(1)
    ?.replace(/<[^>]*>/g, " ")
    .trim()
  if (!raw) return null
  // The server escapes the title, so decode entities ("a &amp; b" → "a & b").
  const decoded = new DOMParser()
    .parseFromString(raw, "text/html")
    .body.textContent?.replace(/\s+/g, " ")
    .trim()
  return decoded || null
}

function usePlanDocumentTitle(threadId: string) {
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
  const title = query.data
    ? artifactTitle(query.data.html, query.data.markdown)
    : null

  useEffect(() => {
    if (!title) return
    const documentTitle = pageTitle(title)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = DEFAULT_TITLE
    }
  }, [title])

  return query
}

function Centered({
  children,
  standalone,
}: {
  children: React.ReactNode
  standalone: boolean
}) {
  return (
    <Box
      className={cn(
        "flex min-w-0 flex-1 items-center justify-center px-4 py-6",
        standalone && "max-md:pt-14 md:p-6"
      )}
    >
      {children}
    </Box>
  )
}

function BackLink({ threadId }: { threadId: string }) {
  return (
    <Link
      to="/agents/$threadId"
      params={{ threadId }}
      className="inline-flex items-center gap-1.5 rounded-badge text-label text-ink-subtle outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary"
    >
      <Icon icon={ArrowLeft} size="sm" />
      Back to conversation
    </Link>
  )
}

export function planSignInHref(): string {
  return loginUrl(currentAuthRedirectPath())
}

export function PlanSignInButton() {
  return (
    <a href={planSignInHref()} className={buttonVariants({ size: "compact" })}>
      Sign in to view this artifact
    </a>
  )
}

export function PlanView({
  threadId,
  standalone = false,
}: {
  threadId: string
  standalone?: boolean
}) {
  const mounted = useIsHydrated()

  const query = usePlanDocumentTitle(threadId)
  const backLink = standalone ? <BackLink threadId={threadId} /> : null

  if (!mounted || query.isLoading) {
    return (
      <Centered standalone={standalone}>
        <Skeleton className="h-48 w-full max-w-reading rounded-panel" />
      </Centered>
    )
  }

  if (query.isError) {
    const status = query.error instanceof PlanApiError ? query.error.status : 0
    return (
      <Centered standalone={standalone}>
        <EmptyState
          icon={status === 401 ? Lock : FileSearch}
          title={
            status === 401
              ? "Please sign in to view this artifact."
              : "This artifact could not be found."
          }
          action={
            status === 401 || backLink ? (
              <Stack gap="md" align="center">
                {status === 401 ? <PlanSignInButton /> : null}
                {backLink}
              </Stack>
            ) : undefined
          }
        />
      </Centered>
    )
  }

  const plan = query.data
  if (!plan?.html.trim() && !plan?.markdown.trim()) {
    return (
      <Centered standalone={standalone}>
        <EmptyState
          media={<Spinner size="lg" className="text-ink-subtle" />}
          title="The agent is still writing the content."
          description="This view will update automatically…"
          action={backLink ?? undefined}
        />
      </Centered>
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {standalone && (
        <Box className="border-b border-line px-4 pt-14 pb-3 md:px-6 md:pt-3">
          {backLink}
        </Box>
      )}
      <PlanReview plan={plan} />
    </div>
  )
}
