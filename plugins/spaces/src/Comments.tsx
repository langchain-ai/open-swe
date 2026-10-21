import { useState } from "react"

import type { Anchor, CommentStatus, PageComment } from "./model"
import { timeAgo } from "./model"

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

export function CommentsPanel({
  threads,
  composing,
  activeId,
  canAccept,
  onCompose,
  onCancelCompose,
  onSelect,
  onReply,
  onSetStatus,
  onAccept,
}: {
  threads: CommentThread[]
  composing: Composing | null
  activeId: string | null
  canAccept: boolean
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

  return (
    <div className="panel-body">
      {composing && (
        <Composer
          composing={composing}
          onSubmit={onCompose}
          onCancel={onCancelCompose}
        />
      )}
      {open.length === 0 && !composing && (
        <p className="muted">
          Select text in the page to comment on it or suggest a change.
        </p>
      )}
      {open.map((thread) => (
        <ThreadCard
          key={thread.id}
          thread={thread}
          active={thread.id === activeId}
          canAccept={canAccept}
          onSelect={onSelect}
          onReply={onReply}
          onSetStatus={onSetStatus}
          onAccept={onAccept}
        />
      ))}
      {closed.length > 0 && (
        <button
          type="button"
          className="link"
          onClick={() => setShowClosed((value) => !value)}
        >
          {showClosed ? "Hide" : "Show"} {closed.length} closed
        </button>
      )}
      {showClosed &&
        closed.map((thread) => (
          <ThreadCard
            key={thread.id}
            thread={thread}
            active={thread.id === activeId}
            canAccept={false}
            onSelect={onSelect}
            onReply={onReply}
            onSetStatus={onSetStatus}
            onAccept={onAccept}
          />
        ))}
    </div>
  )
}

function Composer({
  composing,
  onSubmit,
  onCancel,
}: {
  composing: Composing
  onSubmit: (body: string, suggestion: string | null) => Promise<void>
  onCancel: () => void
}) {
  const suggesting = composing.kind === "suggestion"
  const [body, setBody] = useState("")
  const [suggestion, setSuggestion] = useState(composing.anchor.quote)
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="card composer"
      onSubmit={(event) => {
        event.preventDefault()
        setBusy(true)
        onSubmit(body.trim(), suggesting ? suggestion : null).finally(() =>
          setBusy(false)
        )
      }}
    >
      <blockquote>{composing.anchor.quote}</blockquote>
      {suggesting && (
        <label>
          <span>Replace with</span>
          <textarea
            value={suggestion}
            onChange={(event) => setSuggestion(event.target.value)}
            rows={3}
            autoFocus
          />
        </label>
      )}
      <textarea
        placeholder={suggesting ? "Why? (optional)" : "Add a comment…"}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        rows={3}
        autoFocus={!suggesting}
      />
      <div className="row end">
        <button type="button" className="ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary"
          disabled={
            busy ||
            (suggesting ? suggestion === composing.anchor.quote : !body.trim())
          }
        >
          {suggesting ? "Suggest" : "Comment"}
        </button>
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

function ThreadCard({
  thread,
  active,
  canAccept,
  onSelect,
  onReply,
  onSetStatus,
  onAccept,
}: {
  thread: CommentThread
  active: boolean
  canAccept: boolean
  onSelect: (id: string) => void
  onReply: (parent: string, body: string) => Promise<void>
  onSetStatus: (id: string, status: CommentStatus) => Promise<void>
  onAccept: (thread: CommentThread) => Promise<void>
}) {
  const { comment } = thread
  const [reply, setReply] = useState("")
  const [busy, setBusy] = useState(false)
  const act = (work: Promise<void>) => {
    setBusy(true)
    void work.finally(() => setBusy(false))
  }
  const isOpen = comment.status === "open"

  return (
    <article
      className={`card thread${active ? " active" : ""}${isOpen ? "" : " closed"}`}
      onClick={() => onSelect(thread.id)}
    >
      <header className="row">
        <strong>{comment.author}</strong>
        <span className="muted">{timeAgo(comment.createdAt)}</span>
        {!isOpen && (
          <span className="chip">{STATUS_LABEL[comment.status]}</span>
        )}
      </header>
      {thread.anchored ? (
        <blockquote>{comment.quote}</blockquote>
      ) : (
        <blockquote
          className="orphaned"
          title="This passage is no longer in the page"
        >
          {comment.quote}
        </blockquote>
      )}
      {comment.suggestion !== null && (
        <div className="suggestion">
          <del>{comment.quote}</del>
          <ins>{comment.suggestion}</ins>
        </div>
      )}
      {comment.body && <p>{comment.body}</p>}
      {thread.replies.map(({ id, comment: r }) => (
        <div key={id} className="reply">
          <header className="row">
            <strong>{r.author}</strong>
            <span className="muted">{timeAgo(r.createdAt)}</span>
          </header>
          <p>{r.body}</p>
        </div>
      ))}
      {isOpen && (
        <>
          <form
            className="reply-form"
            onSubmit={(event) => {
              event.preventDefault()
              if (!reply.trim()) return
              act(onReply(thread.id, reply.trim()).then(() => setReply("")))
            }}
          >
            <input
              placeholder="Reply…"
              value={reply}
              onChange={(event) => setReply(event.target.value)}
              onClick={(event) => event.stopPropagation()}
            />
          </form>
          <div className="row end">
            {comment.suggestion !== null ? (
              <>
                <button
                  type="button"
                  className="ghost"
                  disabled={busy}
                  onClick={(event) => {
                    event.stopPropagation()
                    act(onSetStatus(thread.id, "rejected"))
                  }}
                >
                  Reject
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy || !thread.anchored || !canAccept}
                  title={
                    thread.anchored
                      ? undefined
                      : "The passage it replaces is no longer in the page"
                  }
                  onClick={(event) => {
                    event.stopPropagation()
                    act(onAccept(thread))
                  }}
                >
                  Accept
                </button>
              </>
            ) : (
              <button
                type="button"
                className="ghost"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation()
                  act(onSetStatus(thread.id, "resolved"))
                }}
              >
                Resolve
              </button>
            )}
          </div>
        </>
      )}
      {!isOpen && (
        <div className="row end">
          <button
            type="button"
            className="ghost"
            onClick={(event) => {
              event.stopPropagation()
              act(onSetStatus(thread.id, "open"))
            }}
          >
            Reopen
          </button>
        </div>
      )}
    </article>
  )
}
