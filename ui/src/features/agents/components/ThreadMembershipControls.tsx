import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"

import {
  useChangeThreadMembership,
  useThreadMembership,
} from "@/features/agents/lib/membership"
import type { AgentThread } from "@/features/agents/lib/types"

export function ThreadMembershipControls({ thread }: { thread: AgentThread }) {
  const membership = useThreadMembership(thread)
  const change = useChangeThreadMembership(thread.id)
  if (!membership.needsApproval || thread.triggerKind === "slack_bot")
    return null

  if (membership.isError) {
    return (
      <Banner
        intent="error"
        action={
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            onClick={() => void membership.refetch()}
          >
            Retry
          </Button>
        }
      >
        Thread membership could not be loaded.
      </Banner>
    )
  }

  if (!membership.canParticipate) {
    const pending = membership.data?.requestPending === true
    return (
      <Banner
        intent="info"
        action={
          <Button
            size="sm"
            color="secondary"
            disabled={!membership.data || pending || change.isPending}
            onClick={() => change.mutate(null)}
          >
            {pending ? "Request pending" : "Request to join"}
          </Button>
        }
      >
        {pending
          ? `Waiting for @${thread.ownerLogin} to approve your request. You can still view this thread.`
          : `You’re viewing this thread. @${thread.ownerLogin} must approve you before you can participate.`}
      </Banner>
    )
  }

  return (
    <div className="flex flex-col gap-space-2 text-xs text-secondary">
      <span>
        Owned by @{thread.ownerLogin} · Joining requires owner approval
      </span>
      {membership.data?.pendingLogins.map((login) => (
        <div
          key={login}
          className="flex flex-wrap items-center gap-space-2 rounded-md border border-subtle bg-surface-level-2 p-space-2"
        >
          <span className="min-w-0 flex-1 break-words">
            @{login} requested to join
          </span>
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            disabled={change.isPending}
            aria-label={`Deny ${login}'s join request`}
            onClick={() => change.mutate({ login, approve: false })}
          >
            Deny
          </Button>
          <Button
            size="sm"
            color="primary"
            disabled={change.isPending}
            aria-label={`Approve ${login}'s join request`}
            onClick={() => change.mutate({ login, approve: true })}
          >
            Approve
          </Button>
        </div>
      ))}
    </div>
  )
}
