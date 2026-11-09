import { Banner } from "@langchain/macaw-components/Banner"
import type { BannerIntent } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { ArrowsInSimpleIcon } from "@phosphor-icons/react/dist/ssr/ArrowsInSimple"
import { ArrowsOutSimpleIcon } from "@phosphor-icons/react/dist/ssr/ArrowsOutSimple"
import { useState } from "react"

import {
  useAgentThreadGoal,
  useEditAgentThreadGoal,
} from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"
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
      return "the Robin review"
  }
}

const PAUSE_REASONS: Partial<
  Record<NonNullable<AgentThreadGoal["reason"]>, string>
> = {
  stopped: "You stopped the run.",
  requested: "Paused as you asked.",
  no_progress: "The agent kept stopping without progress.",
  failing_commands: "Every command failed three turns in a row.",
  error: "A run failed.",
}

/** What the bar says for each state, or null when there is nothing to show. */
function describe(goal: AgentThreadGoal): {
  intent: BannerIntent
  title: string
  body: string | null
} {
  switch (goal.state) {
    case "working":
      return { intent: "neutral", title: "Goal: working", body: goal.objective }
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
    case "complete":
      return { intent: "success", title: "Goal complete", body: goal.objective }
    case "paused":
      return {
        intent: "neutral",
        title: "Goal paused",
        body: `${PAUSE_REASONS[goal.reason ?? "no_progress"] ?? PAUSE_REASONS.no_progress} Send a message to resume.`,
      }
  }
}

/** Where the thread's Goal mode work stands, above the composer; nothing without
 * Goal mode. Expanding shows the whole goal, which the thread's owner edits in place. */
export function ThreadGoalBar({
  threadId,
  editable,
}: {
  threadId: string
  editable: boolean
}) {
  const goal = useAgentThreadGoal(threadId).data
  const edit = useEditAgentThreadGoal(threadId)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState("")
  if (!goal) return null
  const { intent, title, body } = describe(goal)
  const objective = goal.objective
  const expanded = open && objective !== null
  const toggle = () => {
    if (!expanded && objective) setDraft(objective)
    setOpen(!expanded)
  }
  const save = () => {
    const next = draft.trim()
    if (!next) return
    setOpen(false)
    if (next !== objective) edit.mutate(next)
  }
  return (
    <Banner
      intent={intent}
      title={title}
      action={
        objective ? (
          <IconButton
            icon={expanded ? ArrowsInSimpleIcon : ArrowsOutSimpleIcon}
            label={expanded ? "Collapse goal" : "Expand goal"}
            aria-expanded={expanded}
            size="sm"
            color="secondary"
            variant="plain"
            onClick={toggle}
          />
        ) : null
      }
      className="mb-space-2"
    >
      {body && !(expanded && body === objective) ? (
        <p
          className={cn(
            "text-sm whitespace-pre-wrap",
            !expanded && "line-clamp-3"
          )}
        >
          {body}
        </p>
      ) : null}
      {expanded && editable ? (
        <form
          className={cn(
            "flex flex-col gap-space-2",
            body && body !== objective && "mt-space-2"
          )}
          onSubmit={(event) => {
            event.preventDefault()
            save()
          }}
        >
          <Textarea
            aria-label="Goal"
            size="sm"
            autoFocus
            autoResize
            maxHeight={320}
            maxLength={20_000}
            value={draft}
            onChange={setDraft}
            onKeyDown={(event) => {
              if (event.key === "Escape") setOpen(false)
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault()
                save()
              }
            }}
          />
          <div className="flex items-center justify-end gap-space-2">
            <span className="mr-auto text-xs text-secondary">
              Saving interrupts the agent and hands it the new goal.
            </span>
            <Button
              size="sm"
              variant="outlined"
              color="secondary"
              type="button"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              type="submit"
              disabled={!draft.trim() || draft.trim() === objective}
            >
              Save goal
            </Button>
          </div>
        </form>
      ) : expanded ? (
        <p
          className={cn(
            "max-h-64 overflow-y-auto text-sm whitespace-pre-wrap",
            body && body !== objective && "mt-space-2"
          )}
        >
          {objective}
        </p>
      ) : null}
    </Banner>
  )
}
