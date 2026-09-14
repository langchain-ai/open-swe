import { Link, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

export function PullRequestLinks({
  repo,
  number,
}: {
  repo: string
  number: number
}) {
  const [owner, name] = repo.split("/")
  const navigate = useNavigate()
  const thread = useMutation({
    mutationFn: () => api.openPullRequestThread(repo, number),
    onSuccess: ({ thread_id }) =>
      navigate({ to: "/agents/$threadId", params: { threadId: thread_id } }),
    retry: false,
  })
  return (
    <div className="mt-1 text-xs text-muted-foreground">
      Open in:{" "}
      <a
        className="hover:underline"
        href={`https://github.com/${repo}/pull/${number}`}
        target="_blank"
        rel="noreferrer"
      >
        GitHub
      </a>
      ,{" "}
      <Link
        className="hover:underline"
        to="/agents/reviews/$owner/$repo/$number"
        params={{ owner: owner!, repo: name!, number: String(number) }}
      >
        Review Mode
      </Link>
      ,{" "}
      <button
        type="button"
        className="hover:underline disabled:opacity-50"
        disabled={thread.isPending || thread.isSuccess}
        aria-live="polite"
        onClick={() => thread.mutate()}
      >
        {thread.isPending ? "Opening thread…" : "Open SWE Thread"}
      </button>
      {thread.error && (
        <p role="alert" className="mt-1 text-destructive">
          {thread.error.message}
        </p>
      )}
    </div>
  )
}
