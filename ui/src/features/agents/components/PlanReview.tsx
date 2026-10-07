import { useCallback, useEffect, useRef, useState } from "react"
import type { KeyboardEvent } from "react"

import type { PlanComment, PlanData, PlanTextAnchor } from "@/lib/plan"
import {
  addPlanComment,
  deletePlanComment,
  getPlanComments,
  submitPlanComments,
} from "@/lib/plan"
import { reportError } from "@/lib/errorReporting"
import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import {
  Alert,
  AlertDescription,
} from "@langchain/gtm-platform-design-system/ui/alert"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Shortcut } from "@langchain/gtm-platform-design-system/ui/shortcut"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"
import { AlertTriangle, Check, Copy, FileText } from "@/components/glyphs"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"

const POLL_MS = 4000

async function copyToClipboard(text: string): Promise<boolean> {
  const nav = navigator as { clipboard?: Clipboard }
  try {
    if (window.isSecureContext && nav.clipboard) {
      await nav.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through */
  }
  try {
    const textarea = document.createElement("textarea")
    textarea.value = text
    textarea.setAttribute("readonly", "")
    textarea.style.position = "fixed"
    textarea.style.top = "-9999px"
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    const copied = document.execCommand("copy")
    document.body.removeChild(textarea)
    return copied
  } catch {
    return false
  }
}

export function PlanReview({ plan }: { plan: PlanData }) {
  const [comments, setComments] = useState<Array<PlanComment>>([])
  const [anchor, setAnchor] = useState<PlanTextAnchor | null>(null)
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [focusComment, setFocusComment] = useState<{
    id: string
    key: number
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const commentRefs = useRef(new Map<string, HTMLElement>())
  const commentMutation = useRef(0)
  const format = plan.html.trim() ? "html" : "markdown"
  const content = format === "html" ? plan.html : plan.markdown
  const canComment = format === "html"

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const mutation = commentMutation.current
      try {
        const next = await getPlanComments(plan.threadId)
        if (!cancelled && mutation === commentMutation.current)
          setComments(next)
      } catch {
        /* next poll retries */
      }
    }
    void load()
    const timer = window.setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [plan.threadId])

  const submitComment = useCallback(async () => {
    const body = draft.trim()
    if (!body || !anchor) return
    setPosting(true)
    setError(null)
    commentMutation.current += 1
    try {
      const created = await addPlanComment(plan.threadId, body, anchor)
      setComments((current) => [...current, created])
      setSubmitted(false)
      setAnchor(null)
      setDraft("")
      setFocusComment({ id: created.id, key: Date.now() })
    } catch (commentError) {
      setError((commentError as Error).message)
    } finally {
      setPosting(false)
    }
  }, [anchor, draft, plan.threadId])

  const handleCommentKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Enter" || (!event.metaKey && !event.ctrlKey)) return
      event.preventDefault()
      if (!posting && draft.trim()) void submitComment()
    },
    [draft, posting, submitComment]
  )

  const submitComments = useCallback(async () => {
    setSubmitting(true)
    setError(null)
    try {
      await submitPlanComments(plan.threadId)
      setSubmitted(true)
    } catch (submitError) {
      setError((submitError as Error).message)
    } finally {
      setSubmitting(false)
    }
  }, [plan.threadId])

  const removeComment = useCallback(
    async (id: string) => {
      commentMutation.current += 1
      try {
        await deletePlanComment(plan.threadId, id)
        setComments((current) => current.filter((comment) => comment.id !== id))
        setSubmitted(false)
      } catch (deleteError) {
        reportError({ title: "Couldn't delete comment", error: deleteError })
      }
    },
    [plan.threadId]
  )

  const openComment = useCallback((id: string) => {
    setFocusComment({ id, key: Date.now() })
    commentRefs.current
      .get(id)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }, [])

  const copyPlan = useCallback(async () => {
    setError(null)
    if (await copyToClipboard(content)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } else {
      setError(`Couldn't copy the artifact ${format} to the clipboard.`)
    }
  }, [content, format])

  const ownsComments = comments.some(
    (comment) => comment.author_login === plan.user.login
  )

  return (
    <main
      data-testid="plan-review"
      className="@container flex min-h-0 flex-1 flex-col overflow-hidden bg-canvas text-ink"
    >
      <Stack gap="md" className="min-h-0 w-full flex-1 p-3 md:p-4">
        <Box
          render={<header />}
          className="flex flex-col gap-3 border-b border-line pb-3 @3xl:flex-row @3xl:items-center @3xl:justify-between"
        >
          <Stack gap="none" data-testid="plan-summary" className="min-w-0">
            <Box
              render={<h1 />}
              className="text-page font-semibold tracking-tightish text-ink"
            >
              Artifact
            </Box>
            <Box render={<p />} className="text-meta text-ink-subtle">
              Viewing as {plan.user.name}
            </Box>
          </Stack>
          <Inline data-testid="plan-actions" gap="sm" align="center" wrap>
            <Button
              data-testid="copy-plan"
              variant="outline"
              disabled={!content.trim()}
              onClick={() => void copyPlan()}
            >
              <Icon icon={copied ? Check : Copy} size="sm" />
              {copied
                ? "Copied!"
                : `Copy ${format === "html" ? "HTML" : "Markdown"}`}
            </Button>
          </Inline>
        </Box>

        {error && (
          <Alert tone="risk" icon={AlertTriangle}>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Box
          render={<section />}
          data-testid="plan-document"
          bg="panel"
          border="line"
          radius="panel"
          className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
        >
          {content.trim() ? (
            format === "html" ? (
              <div className="flex min-h-0 min-w-0 flex-1 flex-col @3xl:flex-row">
                <PlanArtifactFrame
                  html={content}
                  comments={comments}
                  onTextSelected={canComment ? setAnchor : undefined}
                  onCommentSelected={openComment}
                  focusCommentId={focusComment?.id ?? null}
                  focusCommentKey={focusComment?.key ?? 0}
                  className="h-full min-h-0 min-w-0 flex-1"
                />
                <Box
                  render={<aside />}
                  data-testid="plan-comments"
                  bg="sidebar"
                  className="flex max-h-1/2 shrink-0 flex-col overflow-y-auto border-t border-line @3xl:max-h-none @3xl:w-80 @3xl:border-t-0 @3xl:border-l"
                >
                  <Stack gap="xs" className="border-b border-line p-3">
                    <Box
                      render={<h2 />}
                      className="text-title font-semibold text-ink"
                    >
                      Comments
                    </Box>
                    <Box render={<p />} className="text-meta text-ink-subtle">
                      Highlight text in the preview to comment.
                    </Box>
                  </Stack>
                  {anchor && (
                    <Stack
                      gap="sm"
                      data-testid="comment-composer"
                      className="border-b border-line p-3"
                    >
                      <Box
                        render={<blockquote />}
                        className="line-clamp-3 border-l-2 border-primary pl-2 text-meta text-ink-subtle"
                      >
                        {anchor.exact}
                      </Box>
                      <Textarea
                        data-testid="comment-input"
                        aria-label="Comment"
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={handleCommentKeyDown}
                        placeholder="Leave a comment"
                        rows={3}
                        autoFocus
                        className="resize-none text-body"
                      />
                      <Inline gap="sm" align="center" justify="end">
                        <Box
                          render={<span aria-hidden="true" />}
                          className="mr-auto"
                        >
                          <Shortcut keys={["mod", "enter"]} />
                        </Box>
                        <Button
                          variant="ghost"
                          size="compact"
                          disabled={posting}
                          onClick={() => {
                            setAnchor(null)
                            setDraft("")
                          }}
                        >
                          Cancel
                        </Button>
                        <Button
                          data-testid="comment-submit"
                          aria-keyshortcuts="Meta+Enter Control+Enter"
                          size="compact"
                          disabled={posting || !draft.trim()}
                          onClick={() => void submitComment()}
                        >
                          {posting ? "Posting…" : "Comment"}
                        </Button>
                      </Inline>
                    </Stack>
                  )}
                  <Box className="min-h-0 flex-1 overflow-y-auto p-3">
                    {comments.length === 0 ? (
                      <Box render={<p />} className="text-meta text-ink-subtle">
                        No comments yet.
                      </Box>
                    ) : (
                      <Stack gap="sm">
                        {comments.map((comment, index) => (
                          <Stack
                            key={comment.id}
                            render={<article />}
                            ref={(element: HTMLElement | null) => {
                              if (element)
                                commentRefs.current.set(comment.id, element)
                              else commentRefs.current.delete(comment.id)
                            }}
                            data-testid="plan-comment"
                            gap="sm"
                            bg="panel"
                            border="line"
                            radius="compact"
                            padding="md"
                          >
                            <button
                              type="button"
                              className="flex w-full cursor-pointer flex-col gap-2 rounded-badge text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
                              onClick={() => openComment(comment.id)}
                            >
                              <span className="text-label font-semibold text-ink">
                                {index + 1}. {comment.author}
                              </span>
                              {comment.anchor && (
                                <blockquote className="line-clamp-2 border-l-2 border-attention pl-2 text-meta text-ink-subtle">
                                  {comment.anchor.exact}
                                </blockquote>
                              )}
                              <span className="block text-body whitespace-pre-wrap text-ink">
                                {comment.body}
                              </span>
                            </button>
                            {comment.author_login === plan.user.login && (
                              <Button
                                type="button"
                                variant="ghost"
                                size="compact"
                                data-testid="comment-delete"
                                className="-ml-2.5 self-start text-ink-subtle hover:text-ink"
                                onClick={() => void removeComment(comment.id)}
                              >
                                Delete
                              </Button>
                            )}
                          </Stack>
                        ))}
                      </Stack>
                    )}
                  </Box>
                  {ownsComments && (
                    <Box className="border-t border-line p-3">
                      <Button
                        className="w-full"
                        disabled={submitting || posting || submitted}
                        onClick={() => void submitComments()}
                      >
                        {submitting
                          ? "Submitting…"
                          : submitted
                            ? "Comments submitted"
                            : "Submit comments"}
                      </Button>
                    </Box>
                  )}
                </Box>
              </div>
            ) : (
              <Box
                data-testid="plan-markdown"
                className="h-full w-full overflow-y-auto p-4 md:p-6"
              >
                <Box className="mx-auto max-w-reading">
                  <Markdown content={content} />
                </Box>
              </Box>
            )
          ) : (
            <EmptyState
              icon={FileText}
              title="The artifact hasn't been written yet."
              className="flex-1"
            />
          )}
        </Box>
      </Stack>
    </main>
  )
}
