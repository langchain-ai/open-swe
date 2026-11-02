import { Banner } from "@langchain/macaw-components/Banner"
import type { BannerIntent } from "@langchain/macaw-components/Banner"

import { useAgentThreadGoal } from "@/features/agents/lib/queries"
import type {
  AgentGoalWait,
  AgentThreadGoal,
} from "@/features/agents/lib/types"

function clock(value: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? null
    : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
}

function waitLabel(wait: AgentGoalWait): string {
  switch (wait.kind) {
    case "wakeup": {
      const at = clock(wait.until)
      return at ? `a wakeup at ${at}` : "a wakeup"
    }
    case "command":
      return "a background command"
    case "monitor":
      return "a monitor"
    case "event":
      return wait.detail ? `events (${wait.detail})` : "events"
    case "review":
      return "the Open SWE review"
  }
}

/** What the bar says for each state, or null when there is nothing to show. */
function describe(goal: AgentThreadGoal): {
  intent: BannerIntent
  title: string
  body: string | null
} {
  switch (goal.state) {
    case "working":
      return { intent: "neutral", title: "Goal: working", body: null }
    case "waiting":
      return {
        intent: "info",
        title: "Goal: waiting",
        body: goal.waits.length
          ? `Wakes on ${goal.waits.map(waitLabel).join(", ")}.`
          : null,
      }
    case "waiting_on_user":
      return goal.reason === "ready"
        ? { intent: "success", title: "Ready for you", body: goal.ask }
        : { intent: "warning", title: "Waiting on you", body: goal.ask }
    case "paused":
      return {
        intent: "neutral",
        title: "Goal paused",
        body:
          goal.reason === "stopped"
            ? "You stopped the run. Send a message to resume."
            : "The agent kept stopping without progress. Send a message to resume.",
      }
  }
}

/** Where the thread's Goal mode work stands, above the composer; nothing without Goal mode. */
export function ThreadGoalBar({ threadId }: { threadId: string }) {
  const goal = useAgentThreadGoal(threadId).data
  if (!goal) return null
  const { intent, title, body } = describe(goal)
  return (
    <Banner intent={intent} title={title} className="mb-space-2">
      {body ? (
        <p className="line-clamp-3 text-sm whitespace-pre-wrap">{body}</p>
      ) : null}
    </Banner>
  )
}
