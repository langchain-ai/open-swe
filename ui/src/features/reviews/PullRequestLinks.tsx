import { Link } from "@tanstack/react-router"

export function PullRequestLinks({
  repo,
  number,
}: {
  repo: string
  number: number
}) {
  const [owner, name] = repo.split("/")
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
    </div>
  )
}
