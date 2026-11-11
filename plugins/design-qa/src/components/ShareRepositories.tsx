import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { KanbanIcon } from "@phosphor-icons/react/dist/ssr/Kanban"

import { useShareRepositories } from "../lib/queries"

/**
 * Asks for repositories Design QA cannot read: none shared yet, or one that
 * stopped being shared, which every call about it then refuses with a 403.
 */
export function ShareRepositories({
  repository,
  unshared = false,
}: {
  repository?: string
  unshared?: boolean
}) {
  const share = useShareRepositories()
  return (
    <EmptyState
      className="py-space-8"
      icon={KanbanIcon}
      title={
        repository
          ? `Share ${repository} with Design QA`
          : unshared
            ? "Share your repositories again"
            : "Choose repositories to review"
      }
      description={
        repository
          ? "Design QA can only read the captures of repositories you share with it. Share this one to see its pull request."
          : unshared
            ? "Design QA can no longer read a repository it was showing. Share the repositories you review to see their pull requests again."
            : "Design QA shows the saved visual captures of pull requests in the repositories you share with it."
      }
      action={
        <Button
          size="sm"
          loading={share.isPending}
          onClick={() => share.mutate(repository)}
        >
          {repository ? `Share ${repository}` : "Choose repositories"}
        </Button>
      }
    />
  )
}

/** A small "share again" for a refusal inside a page that otherwise loaded. */
export function ShareAgainButton({ repository }: { repository: string }) {
  const share = useShareRepositories()
  return (
    <Button
      size="sm"
      color="secondary"
      variant="outlined"
      className="self-start"
      loading={share.isPending}
      onClick={() => share.mutate(repository)}
    >
      Share {repository} again
    </Button>
  )
}
