import { Link, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import { IoLogoGithub } from "react-icons/io5"

import { Button, buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

export const navLink = cn(
  buttonVariants({ variant: "ghost", size: "sm" }),
  "px-1.5 text-muted-foreground"
)

export function PullRequestLinks({
  repo,
  number,
  title,
  onReviewPage = false,
}: {
  repo: string
  number: number
  title: string
  onReviewPage?: boolean
}) {
  const [owner, name] = repo.split("/")
  const navigate = useNavigate()
  const thread = useMutation({
    mutationFn: () => api.openPullRequestThread(repo, number, title),
    onSuccess: ({ thread_id }) =>
      navigate({ to: "/agents/$threadId", params: { threadId: thread_id } }),
    retry: false,
  })
  return (
    <div className="text-xs">
      <span className="flex flex-wrap items-center gap-0.5">
        <Button
          variant="ghost"
          size="sm"
          className="px-1.5 text-muted-foreground"
          disabled={thread.isPending || thread.isSuccess}
          aria-live="polite"
          onClick={() => thread.mutate()}
        >
          {thread.isPending ? "Opening thread…" : "Agent"}
        </Button>
        {!onReviewPage && (
          <>
            <Link
              className={navLink}
              to="/agents/reviews/$owner/$repo/$number"
              params={{ owner: owner!, repo: name!, number: String(number) }}
            >
              Reviewer
            </Link>
            <a
              className={navLink}
              href={`https://github.com/${repo}/pull/${number}`}
              target="_blank"
              rel="noreferrer"
              aria-label="GitHub"
              title="Open on GitHub"
            >
              <IoLogoGithub className="size-3.5" />
            </a>
          </>
        )}
      </span>
      {thread.error && (
        <p role="alert" className="mt-1 text-destructive">
          {thread.error.message}
        </p>
      )}
    </div>
  )
}
