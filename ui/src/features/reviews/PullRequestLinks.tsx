import { Link, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"

import { buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const link = cn(buttonVariants({ variant: "ghost", size: "sm" }), "px-1.5")

export function PullRequestLinks({
  repo,
  number,
  title,
}: {
  repo: string
  number: number
  title: string
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
        <button
          type="button"
          className={link}
          disabled={thread.isPending || thread.isSuccess}
          aria-live="polite"
          onClick={() => thread.mutate()}
        >
          {thread.isPending ? "Opening thread…" : "Agent"}
        </button>
        <Link
          className={link}
          to="/agents/reviews/$owner/$repo/$number"
          params={{ owner: owner!, repo: name!, number: String(number) }}
        >
          Reviewer
        </Link>
        <a
          className={link}
          href={`https://github.com/${repo}/pull/${number}`}
          target="_blank"
          rel="noreferrer"
        >
          GitHub
        </a>
      </span>
      {thread.error && (
        <p role="alert" className="mt-1 text-destructive">
          {thread.error.message}
        </p>
      )}
    </div>
  )
}
