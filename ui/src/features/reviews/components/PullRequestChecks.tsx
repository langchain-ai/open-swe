import type { OpenPullRequest } from "@/lib/api"

const inlineLimit = 3

export function PullRequestChecks({ pr }: { pr: OpenPullRequest }) {
  if (pr.failingChecks.length === 0 && pr.pendingChecks.length === 0)
    return null
  const overflow = pr.failingChecks.slice(inlineLimit)
  return (
    <div className="space-y-1 text-xs">
      {pr.failingChecks.length > 0 && (
        <p className="text-destructive">
          {pr.failingChecks.slice(0, inlineLimit).map((name, index) => (
            <span key={`${name}-${index}`}>
              {index > 0 && <span aria-hidden="true"> · </span>}
              {name}
            </span>
          ))}
        </p>
      )}
      {overflow.length > 0 && (
        <details className="text-destructive">
          <summary className="cursor-pointer">+{overflow.length} more</summary>
          <ul className="mt-1 space-y-1">
            {overflow.map((name, index) => (
              <li key={`${name}-${index}`}>{name}</li>
            ))}
          </ul>
        </details>
      )}
      {pr.pendingChecks.length > 0 && (
        <details className="text-muted-foreground">
          <summary className="cursor-pointer">
            {pr.pendingChecks.length} pending
          </summary>
          <ul className="mt-1">
            {pr.pendingChecks.map((name, index) => (
              <li key={`${name}-${index}`}>{name}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
