import { Avatar } from "@langchain/macaw-components/Avatar"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useState } from "react"

import type { Profile } from "@/lib/queries"
import type { Anchor, CommentStatus, PageComment } from "@/lib/model"
import { colorFor, timeAgo } from "@/lib/model"

export interface CommentThread {
  id: string
  comment: PageComment
  replies: Array<{ id: string; comment: PageComment }>
  /** Whether its passage is still in the page. */
  anchored: boolean
}

export interface Composing {
  anchor: Anchor
  kind: "comment" | "suggestion"
}

type Act = (work: () => Promise<void>) => void

export function CommentsPanel({
  threads,
  people,
  composing,
  activeId,
  canAccept,
  act,
  onCompose,
  onCancelCompose,
  onSelect,
  onReply,
  onSetStatus,
  onAccept,
}: {
  threads: CommentThread[]
  people: Map<string, Profile>
  composing: Composing | null
  activeId: string | null
  canAccept: boolean
  act: Act
  onCompose: (body: string, suggestion: string | null) => Promise<void>
  onCancelCompose: () => void
  onSelect: (id: string) => void
  onReply: (parent: string, body: string) => Promise<void>
  onSetStatus: (id: string, status: CommentStatus) => Promise<void>
  onAccept: (thread: CommentThread) => Promise<void>
}) {
  const [showClosed, setShowClosed] = useState(false)
  const open = threads.filter((thread) => thread.comment.status === "open")
  const closed = threads.filter((thread) => thread.comment.status !== "open")
  const card = (thread: CommentThread) => (
    <ThreadCard
      key={thread.id}
      thread={thread}
      people={people}
      active={thread.id === activeId}
      canAccept={canAccept && thread.comment.status === "open"}
      act={act}
      onSelect={onSelect}
      onReply={onReply}
      onSetStatus={onSetStatus}
      onAccept={onAccept}
    />
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-space-3 overflow-y-auto p-space-3">
      {composing && (
        <Composer
          composing={composing}
          act={act}
          onSubmit={onCompose}
          onCancel={onCancelCompose}
        />
      )}
      {open.length === 0 && !composing && (
        <p className="text-sm text-secondary">
          Select text in the page to comment on it or suggest a change.
        </p>
      )}
      {open.map(card)}
      {closed.length > 0 && (
        <button
          type="button"
          className="self-start text-xs text-secondary hover:text-primary"
          onClick={() => setShowClosed((value) => !value)}
        >
          {showClosed ? "Hide" : "Show"} {closed.length} closed
        </button>
      )}
      {showClosed && closed.map(card)}
    </div>
  )
}

function Quote({ text, orphaned }: { text: string; orphaned?: boolean }) {
  return (
    <blockquote
      title={orphaned ? "This passage is no longer in the page" : undefined}
      className={`line-clamp-3 border-l-2 border-default pl-space-2 text-xs whitespace-pre-wrap text-secondary ${orphaned ? "line-through" : ""}`}
    >
      {text}
    </blockquote>
  )
}

function Composer({
  composing,
  act,
  onSubmit,
  onCancel,
}: {
  composing: Composing
  act: Act
  onSubmit: (body: string, suggestion: string | null) => Promise<void>
  onCancel: () => void
}) {
  const suggesting = composing.kind === "suggestion"
  const [body, setBody] = useState("")
  const [suggestion, setSuggestion] = useState(composing.anchor.quote)
  const ready = suggesting
    ? suggestion !== composing.anchor.quote
    : Boolean(body.trim())
  return (
    <form
      className="flex flex-col gap-space-2 rounded-lg border border-default bg-surface-level-1 p-space-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (ready)
          act(() => onSubmit(body.trim(), suggesting ? suggestion : null))
      }}
    >
      <Quote text={composing.anchor.quote} />
      {suggesting && (
        <Textarea
          label="Replace with"
          value={suggestion}
          onChange={setSuggestion}
          rows={3}
          autoFocus
        />
      )}
      <Textarea
        placeholder={suggesting ? "Why? (optional)" : "Add a comment…"}
        value={body}
        onChange={setBody}
        rows={3}
        autoFocus={!suggesting}
      />
      <div className="flex justify-end gap-space-2">
        <Button type="button" size="sm" variant="plain" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!ready}>
          {suggesting ? "Suggest" : "Comment"}
        </Button>
      </div>
    </form>
  )
}

const STATUS_LABEL: Record<CommentStatus, string> = {
  open: "Open",
  resolved: "Resolved",
  accepted: "Accepted",
  rejected: "Rejected",
}

function Author({
  login,
  at,
  people,
}: {
  login: string
  at: string
  people: Map<string, Profile>
}) {
  const person = people.get(login.toLowerCase())
  return (
    <div className="flex items-center gap-space-2">
      <Avatar
        size="sm"
        shape="circle"
        label={person?.name ?? login}
        imageUrl={person?.picture ?? undefined}
        color={colorFor(login)}
      />
      <span className="text-sm font-medium text-primary">
        {person?.name ?? login}
      </span>
      <span className="text-xs text-tertiary">{timeAgo(at)}</span>
    </div>
  )
}

function ThreadCard({
  thread,
  people,
  active,
  canAccept,
  act,
  onSelect,
  onReply,
  onSetStatus,
  onAccept,
}: {
  thread: CommentThread
  people: Map<string, Profile>
  active: boolean
  canAccept: boolean
  act: Act
  onSelect: (id: string) => void
  onReply: (parent: string, body: string) => Promise<void>
  onSetStatus: (id: string, status: CommentStatus) => Promise<void>
  onAccept: (thread: CommentThread) => Promise<void>
}) {
  const { comment } = thread
  const [reply, setReply] = useState("")
  const isOpen = comment.status === "open"
  const isSuggestion = comment.suggestion !== null

  return (
    <article
      onClick={() => onSelect(thread.id)}
      className={`flex flex-col gap-space-2 rounded-lg border bg-surface-level-1 p-space-3 ${
        active ? "border-brand" : "border-default"
      } ${isOpen ? "" : "opacity-70"}`}
    >
      <div className="flex items-center justify-between gap-space-2">
        <Author login={comment.author} at={comment.createdAt} people={people} />
        {!isOpen && (
          <Badge size="xxs" color="secondary">
            {STATUS_LABEL[comment.status]}
          </Badge>
        )}
      </div>
      <Quote text={comment.quote} orphaned={!thread.anchored} />
      {isSuggestion && (
        <div className="flex flex-col gap-0.5 text-sm">
          <del className="text-error">{comment.quote}</del>
          <ins className="text-success no-underline">{comment.suggestion}</ins>
        </div>
      )}
      {comment.body && (
        <p className="text-sm whitespace-pre-wrap text-primary">
          {comment.body}
        </p>
      )}
      {thread.replies.map(({ id, comment: r }) => (
        <div
          key={id}
          className="flex flex-col gap-space-1 border-t border-default pt-space-2"
        >
          <Author login={r.author} at={r.createdAt} people={people} />
          <p className="text-sm whitespace-pre-wrap text-primary">{r.body}</p>
        </div>
      ))}
      {isOpen ? (
        <>
          <form
            onClick={(event) => event.stopPropagation()}
            onSubmit={(event) => {
              event.preventDefault()
              const text = reply.trim()
              if (!text) return
              act(async () => {
                await onReply(thread.id, text)
                setReply("")
              })
            }}
          >
            <input
              placeholder="Reply…"
              value={reply}
              onChange={(event) => setReply(event.target.value)}
              className="h-8 w-full rounded-md border border-default bg-transparent px-space-2 text-sm text-primary outline-none focus:border-brand"
            />
          </form>
          <div
            className="flex justify-end gap-space-1"
            onClick={(event) => event.stopPropagation()}
          >
            {isSuggestion ? (
              <>
                <Button
                  size="xs"
                  variant="plain"
                  onClick={() => act(() => onSetStatus(thread.id, "rejected"))}
                >
                  Reject
                </Button>
                <Button
                  size="xs"
                  disabled={!thread.anchored || !canAccept}
                  title={
                    thread.anchored
                      ? undefined
                      : "The passage it replaces is no longer in the page"
                  }
                  onClick={() => act(() => onAccept(thread))}
                >
                  Accept
                </Button>
              </>
            ) : (
              <Button
                size="xs"
                variant="plain"
                onClick={() => act(() => onSetStatus(thread.id, "resolved"))}
              >
                Resolve
              </Button>
            )}
          </div>
        </>
      ) : (
        <div
          className="flex justify-end"
          onClick={(event) => event.stopPropagation()}
        >
          <Button
            size="xs"
            variant="plain"
            onClick={() => act(() => onSetStatus(thread.id, "open"))}
          >
            Reopen
          </Button>
        </div>
      )}
    </article>
  )
}
