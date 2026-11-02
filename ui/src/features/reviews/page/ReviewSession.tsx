import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { RadioButton } from "@langchain/macaw-components/RadioButton"
import { RadioGroup } from "@langchain/macaw-components/RadioGroup"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { useQuery } from "@tanstack/react-query"
import { useState } from "react"

import type { FollowUpBehavior } from "@/lib/api"
import { useAgentThread } from "@/features/agents/lib/queries"
import {
  readableRangeLabel,
  rangeBounds,
} from "@/features/reviews/lib/lineRange"
import { useReviewChat } from "@/features/reviews/lib/reviewKeys"
import {
  reviewSessionKey,
  useReviewSession,
  useReviewSessions,
  type AgentComment,
  type Handoff,
} from "@/features/reviews/lib/reviewSession"
import { AgentMark } from "./AgentMark"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"
import { plural } from "./text"

/**
 * The strip under the header while a review session runs or its comments are
 * on their way: what the agent has, what it doesn't, and the one way to send.
 */
export function ReviewSessionBar({ pr }: { pr: PullRequestRef }) {
  const chat = useReviewChat(pr).data
  const session = useReviewSession(pr)
  if (!chat?.thread_id || (!session.active && !session.handoff)) return null
  return (
    <SessionStrips
      pr={pr}
      threadId={chat.thread_id}
      chatAvailable={chat.available}
    />
  )
}

function SessionStrips({
  pr,
  threadId,
  chatAvailable,
}: {
  pr: PullRequestRef
  threadId: string
  /** Only the chat delivers a batch; without it, drafts wait. */
  chatAvailable: boolean
}) {
  const key = reviewSessionKey(pr)
  const session = useReviewSession(pr)
  const running = useAgentThread(threadId).data?.status === "running"
  const draftsOpen = useReviewPage((state) => state.sessionDraftsOpen)
  const setDraftsOpen = useReviewPage((state) => state.setSessionDraftsOpen)
  const abandon = useReviewSessions((state) => state.abandon)
  const [confirmAbandon, setConfirmAbandon] = useState(false)
  const count = session.comments.length
  const endSession = () => {
    if (count === 0) abandon(key)
    else setConfirmAbandon(true)
  }
  return (
    <>
      {session.handoff && (
        <HandoffStrip pr={pr} handoff={session.handoff} running={running} />
      )}
      {session.active && (
        <section
          aria-label="Review session"
          className="flex shrink-0 flex-wrap items-center gap-x-space-3 gap-y-space-1 border-b border-default bg-brand-subtle px-space-3 py-space-2 text-xs"
        >
          <AgentMark />
          <p className="min-w-0 flex-1">
            <span className="font-semibold text-primary">
              Review session active
            </span>
            <span className="text-secondary">
              {" "}
              · Agent comments stay unsent until you finish.{" "}
              {running
                ? "Current work continues."
                : "Nothing starts until you send."}
            </span>
            {session.error && (
              <span role="alert" className="block text-error-secondary">
                {session.error}
              </span>
            )}
          </p>
          <Badge size="xs" color="secondary" aria-live="polite">
            {`${plural(count, "unsent draft")}`}
          </Badge>
          <Button
            size="sm"
            color="secondary"
            variant="plain"
            onClick={endSession}
          >
            End session
          </Button>
          <Button size="sm" onClick={() => setDraftsOpen(true)}>
            Finish &amp; review comments
          </Button>
        </section>
      )}
      <SessionDrafts
        pr={pr}
        open={draftsOpen}
        onOpenChange={setDraftsOpen}
        running={running}
        chatAvailable={chatAvailable}
      />
      <Dialog open={confirmAbandon} onOpenChange={setConfirmAbandon}>
        <DialogContent
          title="End the review session?"
          description={`${plural(count, "unsent draft")} for Open SWE will be deleted. Nothing is sent.`}
          showClose={false}
          className="w-[min(28rem,calc(100vw-2rem))]"
        >
          <div className="flex justify-end gap-space-2">
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              onClick={() => setConfirmAbandon(false)}
            >
              Keep reviewing
            </Button>
            <Button
              size="xs"
              color="error"
              onClick={() => {
                abandon(key)
                setConfirmAbandon(false)
              }}
            >
              Delete drafts
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** Where the last batch is: on its way, waiting behind the current run, or being worked on. */
function HandoffStrip({
  pr,
  handoff,
  running,
}: {
  pr: PullRequestRef
  handoff: Handoff
  running: boolean
}) {
  const key = reviewSessionKey(pr)
  const withdraw = useReviewSessions((state) => state.withdraw)
  const focusChat = useReviewPage((state) => state.focusChat)
  const comments = plural(handoff.comments.length, "comment")
  const [title, detail] =
    handoff.status === "scheduled"
      ? handoff.withdrawing
        ? ["Taking your comments back…", "They return to your drafts."]
        : [
            "Scheduled after current run",
            `One batch · ${comments} · Not delivered yet`,
          ]
      : handoff.status === "delivered"
        ? [running ? "Open SWE is working" : "Delivered", `On your ${comments}`]
        : [`Sending ${comments}…`, "Not delivered yet"]
  const pendingWork =
    handoff.status === "sending" ||
    handoff.status === "sent" ||
    handoff.withdrawing
  return (
    <section
      aria-label="Agent comments sent"
      aria-live="polite"
      className="flex shrink-0 flex-wrap items-center gap-x-space-3 gap-y-space-1 border-b border-default bg-surface-level-2 px-space-3 py-space-2 text-xs"
    >
      {pendingWork || handoff.status === "delivered" ? (
        <Spinner size="sm" />
      ) : (
        <AgentMark />
      )}
      <p className="min-w-0 flex-1">
        <span className="font-semibold text-primary">{title}</span>
        <span className="text-secondary"> · {detail}</span>
      </p>
      {handoff.status === "scheduled" && !handoff.withdrawing && (
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          onClick={() => {
            // The chat withdraws it, so it has to be on screen.
            focusChat()
            withdraw(key)
          }}
        >
          Return to drafts
        </Button>
      )}
      <Button size="sm" color="secondary" variant="plain" onClick={focusChat}>
        View in chat
      </Button>
    </section>
  )
}

/** The last look before the drafts go: edit or drop each, then choose when the agent gets them. */
function SessionDrafts({
  pr,
  open,
  onOpenChange,
  running,
  chatAvailable,
}: {
  pr: PullRequestRef
  open: boolean
  onOpenChange: (open: boolean) => void
  running: boolean
  chatAvailable: boolean
}) {
  const key = reviewSessionKey(pr)
  const session = useReviewSession(pr)
  const headSha = useQuery(reviewQueries.detail(pr)).data?.head_sha ?? null
  const send = useReviewSessions((state) => state.send)
  const clearError = useReviewSessions((state) => state.clearError)
  const focusChat = useReviewPage((state) => state.focusChat)
  const [delivery, setDelivery] = useState<FollowUpBehavior>("queue")
  const ready = session.comments.filter((comment) => comment.body.trim())
  const blank = session.comments.length - ready.length
  const blocked = session.handoff !== null || !chatAvailable
  const canSend = !blocked && blank === 0 && ready.length > 0
  const redirect = running && delivery === "steer"
  const submitLabel = !running
    ? "Send & start agent"
    : redirect
      ? "Redirect current run"
      : "Send after current run"
  const submit = () => {
    if (!canSend) return
    send(key, running ? delivery : "queue", headSha)
    onOpenChange(false)
    // The chat carries the batch, so it has to be on screen.
    focusChat()
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) clearError(key)
        onOpenChange(next)
      }}
    >
      <DialogContent
        title="Review agent comments"
        description="For this pull request's review chat. Nothing here is posted to GitHub."
        className="flex max-h-[min(44rem,calc(100vh-4rem))] w-[min(40rem,calc(100vw-2rem))] flex-col"
        childrenClassName="flex min-h-0 flex-1 flex-col gap-space-3 overflow-hidden"
      >
        <p className="text-xs text-secondary">
          {plural(session.comments.length, "comment")} not sent yet.{" "}
          {running
            ? "Open SWE's current run continues until you choose otherwise."
            : "Open SWE is idle; nothing starts until you send."}
        </p>
        {session.error && <Banner intent="error">{session.error}</Banner>}
        <ol className="-mx-space-1 flex min-h-0 flex-1 flex-col gap-space-2 overflow-y-auto px-space-1">
          {session.comments.length === 0 ? (
            <li className="rounded-lg border border-dashed border-default p-space-4 text-center text-xs text-secondary">
              No agent comments yet. Select lines in the diff and choose
              <span className="font-medium text-primary">
                {" "}
                Draft agent comment
              </span>
              .
            </li>
          ) : (
            session.comments.map((comment) => (
              <DraftItem
                key={comment.id}
                pr={pr}
                comment={comment}
                headSha={headSha}
                onShow={() => onOpenChange(false)}
              />
            ))
          )}
        </ol>
        {running && (
          <RadioGroup
            aria-label="When Open SWE gets these comments"
            className="gap-space-2 rounded-lg border border-default p-space-3"
            value={delivery}
            onValueChange={(value) =>
              setDelivery(value === "steer" ? "steer" : "queue")
            }
          >
            <RadioButton
              value="queue"
              className="items-start"
              label={
                <>
                  <span className="font-medium">
                    After the current run finishes
                  </span>
                  <span className="block text-xs text-secondary">
                    Work in progress isn&apos;t interrupted. Open SWE gets every
                    comment together when it&apos;s done, and you can return
                    them to drafts until then.
                  </span>
                </>
              }
            />
            <RadioButton
              value="steer"
              className="items-start"
              label={
                <>
                  <span className="font-medium">
                    Redirect the current run now
                  </span>
                  <span className="block text-xs text-secondary">
                    Open SWE reads the comments during its current work. It
                    doesn&apos;t stop, but it may set aside what it was doing.
                  </span>
                </>
              }
            />
          </RadioGroup>
        )}
        {redirect && (
          <Banner intent="warning">
            This changes what the running agent is doing now.
          </Banner>
        )}
        {blank > 0 && (
          <Banner intent="info">
            {blank === 1
              ? "One draft is empty. Write it or remove it before sending."
              : `${blank} drafts are empty. Write them or remove them before sending.`}
          </Banner>
        )}
        {!chatAvailable && (
          <Banner intent="info">
            Chat is unavailable right now, so nothing can be sent. Your drafts
            are kept.
          </Banner>
        )}
        {session.handoff && (
          <Banner intent="info">
            Your last batch is still on its way. Return it to drafts to send
            everything together, or send these once Open SWE has it.
          </Banner>
        )}
        <div className="flex items-center justify-end gap-space-2">
          <span className="mr-auto text-xs text-secondary">
            {plural(ready.length, "comment")} · One message · Nothing posted to
            GitHub
          </span>
          <Button
            size="sm"
            color="secondary"
            variant="outlined"
            onClick={() => onOpenChange(false)}
          >
            Keep reviewing
          </Button>
          <Button size="sm" disabled={!canSend} onClick={submit}>
            <AgentMark />
            {submitLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function DraftItem({
  pr,
  comment,
  headSha,
  onShow,
}: {
  pr: PullRequestRef
  comment: AgentComment
  headSha: string | null
  onShow: () => void
}) {
  const key = reviewSessionKey(pr)
  const edit = useReviewSessions((state) => state.edit)
  const remove = useReviewSessions((state) => state.remove)
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const where = readableRangeLabel(comment.range)
  const stale = Boolean(
    headSha && comment.headSha && comment.headSha !== headSha
  )
  const selected = comment.excerpts
    .flatMap((excerpt) =>
      excerpt.snippet.split("\n").filter((line) => line.startsWith(">"))
    )
    .map((line) => line.replace(/^> *\d+ \| ?/, ""))
    .join("\n")
  return (
    <li className="flex flex-col gap-space-1 rounded-lg border border-default p-space-2">
      <div className="flex min-w-0 items-center gap-space-2 text-xs">
        <Button
          size="xs"
          color="secondary"
          variant="plain"
          className="h-auto min-w-0 justify-start border-0 px-0 font-mono hover:underline"
          aria-label={`Show ${comment.path} ${where} in the diff`}
          onClick={() => {
            const { hi, side } = rangeBounds(comment.range)
            jumpTo({ kind: "line", path: comment.path, line: hi, side })
            onShow()
          }}
        >
          <span className="truncate">{comment.path}</span>
          <span className="shrink-0 font-sans text-secondary">{where}</span>
        </Button>
        {stale && (
          <Badge
            size="xs"
            color="warning"
            title={`Written at ${comment.headSha?.slice(0, 7)}; the pull request has new commits`}
          >
            Older revision
          </Badge>
        )}
        <span className="flex-1" />
        <IconButton
          icon={TrashIcon}
          label={`Remove the draft on ${comment.path} ${where}`}
          size="xs"
          color="secondary"
          variant="plain"
          onClick={() => remove(key, comment.id)}
        />
      </div>
      {selected && (
        <pre className="max-h-24 overflow-auto rounded-md bg-surface-level-2 px-space-2 py-space-1 font-mono text-xxs text-secondary">
          {selected}
        </pre>
      )}
      <Textarea
        size="md"
        aria-label={`Comment on ${comment.path} ${where}`}
        rows={2}
        resize="vertical"
        value={comment.body}
        onChange={(body) => edit(key, comment.id, body)}
      />
    </li>
  )
}
