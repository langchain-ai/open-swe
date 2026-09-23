import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { StreamProvider, useStreamContext } from "@langchain/react"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowUpIcon,
  CodeIcon,
  SparkleIcon,
  XIcon,
} from "@phosphor-icons/react"
import type { BaseMessage } from "@langchain/core/messages"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Empty, EmptyDescription } from "@/components/ui/empty"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
import { api, reviewChatApiBase } from "@/lib/api"
import { createDashboardClient, dashboardFetch } from "@/lib/langgraph-client"
import {
  collectStructuredEntities,
  parseStructuredInput,
} from "@/features/agents/lib/structuredInputMessages"

// --- Composer bridge ---------------------------------------------------------
//
// Lets the diff column (a separate subtree) attach a code reference to the
// active chat composer. The reference shows as a removable pill; the code is
// only serialized into the message text on send. The chat tab may not be
// mounted when "add to chat" fires, so attachments are stashed until ChatBody
// registers its sink.

export interface ChatAttachment {
  id: string
  path: string
  // e.g. "R35-37" / "L12" — the side+line range shown after the filename.
  lineLabel: string
  language: string
  snippet: string
}

interface ReviewChatComposer {
  addAttachment: (attachment: ChatAttachment) => void
  registerSink: (fn: ((attachment: ChatAttachment) => void) | null) => void
}

const ReviewChatComposerContext = createContext<ReviewChatComposer | null>(null)

export function ReviewChatComposerProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const sinkRef = useRef<((attachment: ChatAttachment) => void) | null>(null)
  const pendingRef = useRef<Array<ChatAttachment>>([])
  const value = useMemo<ReviewChatComposer>(
    () => ({
      addAttachment: (attachment) => {
        if (sinkRef.current) sinkRef.current(attachment)
        else pendingRef.current.push(attachment)
      },
      registerSink: (fn) => {
        sinkRef.current = fn
        if (fn && pendingRef.current.length > 0) {
          for (const attachment of pendingRef.current) fn(attachment)
          pendingRef.current = []
        }
      },
    }),
    []
  )
  return (
    <ReviewChatComposerContext.Provider value={value}>
      {children}
    </ReviewChatComposerContext.Provider>
  )
}

export function useReviewChatComposer(): ReviewChatComposer | null {
  return useContext(ReviewChatComposerContext)
}

function attachmentBasename(path: string): string {
  const idx = path.lastIndexOf("/")
  return idx === -1 ? path : path.slice(idx + 1)
}

function attachmentPillLabel(attachment: ChatAttachment): string {
  return `${attachmentBasename(attachment.path)}:${attachment.lineLabel}`
}

// Serialize attachments as fenced code blocks ahead of the prose so the model
// receives the code as context. The UI renders pills instead (see parse below).
function serializeMessage(
  text: string,
  attachments: Array<ChatAttachment>
): string {
  const blocks = attachments.map(
    (a) =>
      `\`${a.path}:${a.lineLabel}\`\n\`\`\`${a.language}\n${a.snippet}\n\`\`\``
  )
  return [...blocks, text.trim()].filter(Boolean).join("\n\n")
}

interface ParsedAttachment {
  label: string
  language: string
  code: string
}

// Pull the leading attachment blocks (which serializeMessage always writes
// first) back out of a sent message so the bubble can render them as pills.
function parseUserMessage(content: string): {
  attachments: Array<ParsedAttachment>
  text: string
} {
  const attachments: Array<ParsedAttachment> = []
  let rest = content
  const re = /^`([^`\n]+)`\n```([\w.-]*)\n([\s\S]*?)\n```\n*/
  let match = re.exec(rest)
  while (match && match.index === 0) {
    const loc = match[1] ?? ""
    const slash = loc.lastIndexOf("/")
    attachments.push({
      label: slash === -1 ? loc : loc.slice(slash + 1),
      language: match[2] ?? "",
      code: match[3] ?? "",
    })
    rest = rest.slice(match[0].length)
    match = re.exec(rest)
  }
  return { attachments, text: rest.trim() }
}

function AttachmentPill({
  label,
  onRemove,
}: {
  label: string
  onRemove?: () => void
}) {
  return (
    <span className="inline-flex max-w-[200px] items-center gap-1 rounded-md border border-border bg-muted/60 py-0.5 pr-1 pl-1.5 text-[11px] text-foreground">
      <CodeIcon className="size-3 shrink-0 text-muted-foreground" />
      <span className="truncate font-mono">{label}</span>
      {onRemove && (
        <TooltipIconButton
          size="icon-xs"
          label="Remove attachment"
          onClick={onRemove}
        >
          <XIcon />
        </TooltipIconButton>
      )}
    </span>
  )
}

const SUGGESTED_PROMPTS = [
  "Summarize the changes in this PR",
  "Walk me through the review findings",
  "What are the riskiest parts of this change?",
]

function messageType(message: BaseMessage): string {
  const candidate = message as unknown as {
    getType?: () => string
    type?: string
    role?: string
  }
  return candidate.getType?.() ?? candidate.type ?? candidate.role ?? "ai"
}

function messageText(content: BaseMessage["content"]): string {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return ""
  return content
    .map((block) => {
      if (typeof block === "string") return block
      if (typeof block === "object" && "text" in block) {
        const text = (block as { text?: unknown }).text
        return typeof text === "string" ? text : ""
      }
      return ""
    })
    .filter(Boolean)
    .join("\n")
}

// --- View --------------------------------------------------------------------

function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-1 flex-col gap-4 p-4">
      <p className="text-[13px] text-foreground">
        I've reviewed this PR. Ask me about the diff, the findings, or the
        surrounding code — I have read-only access to the repository.
      </p>
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          Suggested prompts
        </span>
        {SUGGESTED_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onPick(prompt)}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-foreground hover:bg-muted/60"
          >
            <SparkleIcon className="size-4 shrink-0 text-muted-foreground" />
            {prompt}
          </button>
        ))}
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="flex flex-1 flex-col gap-4 p-4">
      <div className="flex justify-end">
        <Skeleton className="h-12 w-2/5 rounded-lg" />
      </div>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-4/5" />
        <Skeleton className="h-4 w-3/5" />
      </div>
    </div>
  )
}

function ChatBody() {
  const composer = useReviewChatComposer()
  const stream = useStreamContext()
  const [value, setValue] = useState("")
  const [attachments, setAttachments] = useState<Array<ChatAttachment>>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const prevTopRef = useRef(0)
  const messages = stream.messages
  const busy = stream.isLoading
  // True during the one-time getState hydration when switching to / loading an
  // existing thread, before its messages have arrived.
  const hydrating = stream.isThreadLoading

  // Receive "add to chat" attachments from the diff column as composer pills.
  useEffect(() => {
    if (!composer) return
    composer.registerSink((attachment) =>
      setAttachments((prev) =>
        prev.some((a) => a.id === attachment.id) ? prev : [...prev, attachment]
      )
    )
    return () => composer.registerSink(null)
  }, [composer])

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== id))
  }, [])

  const send = useCallback(
    (text: string, atts: Array<ChatAttachment>) => {
      const trimmed = text.trim()
      const first = atts[0]
      if ((!trimmed && !first) || busy) return
      const content = serializeMessage(trimmed, atts)
      void stream.submit({ messages: [{ type: "human", content }] })
    },
    [busy, stream]
  )

  const structuredEntities = collectStructuredEntities(
    messages
      .filter((message) => messageType(message) === "human")
      .map((message) => messageText(message.content))
  )
  const visible: Array<{
    message: BaseMessage
    content: string
    structured?: ReturnType<typeof parseStructuredInput>
  }> = messages.flatMap((message) => {
    const type = messageType(message)
    if (type !== "human" && type !== "ai") return []
    const content = messageText(message.content)
    if (type === "ai") return content.trim() ? [{ message, content }] : []
    const parsed = parseStructuredInput(content, structuredEntities)
    if (parsed.type === "entity" || !parsed.content.trim()) return []
    return [{ message, content: parsed.content, structured: parsed }]
  })

  const submitComposer = () => {
    send(value, attachments)
    setValue("")
    setAttachments([])
  }

  // Show the loading placeholder (not the empty/intro state) while an existing
  // conversation hydrates, so a chat with messages never flashes its greeting.
  const showEmpty = visible.length === 0 && !busy
  const showLoading = showEmpty && hydrating
  const showMessages = !showEmpty && !showLoading

  // Auto-scroll is fully contained to the messages list (scrollTop assignment,
  // never scrollIntoView) so it can't bubble up and move the diff column. We
  // pause auto-scroll when the user scrolls up and resume when near the bottom.
  useLayoutEffect(() => {
    if (!showMessages) return
    const el = scrollRef.current
    if (!el || !autoScrollRef.current) return
    el.scrollTop = el.scrollHeight
    prevTopRef.current = el.scrollTop
  }, [showMessages, messages, busy])

  useEffect(() => {
    if (!showMessages) return
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      const top = el.scrollTop
      const nearBottom = el.scrollHeight - top - el.clientHeight <= 24
      if (top < prevTopRef.current - 1) autoScrollRef.current = false
      else if (nearBottom) autoScrollRef.current = true
      prevTopRef.current = top
    }
    el.addEventListener("scroll", onScroll, { passive: true })
    return () => el.removeEventListener("scroll", onScroll)
  }, [showMessages])

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {showLoading ? (
        <LoadingState />
      ) : showEmpty ? (
        <EmptyState
          onPick={(prompt) => {
            send(prompt, attachments)
            setAttachments([])
          }}
        />
      ) : (
        <div
          ref={scrollRef}
          className="flex flex-1 flex-col gap-4 overflow-y-auto p-4"
        >
          {visible.map(({ message, content, structured }, index) => {
            const isUser = messageType(message) === "human"
            if (!isUser) {
              return (
                <div key={message.id ?? index} className="flex justify-start">
                  <div className="w-full text-[13px] text-foreground">
                    <Markdown content={content} />
                  </div>
                </div>
              )
            }
            const parsed = parseUserMessage(content)
            const isSystem =
              structured?.type === "message" &&
              structured.senderKind === "system"
            return (
              <div
                key={message.id ?? index}
                className={isSystem ? "flex justify-start" : "flex justify-end"}
                data-message-sender-kind={
                  structured?.type === "message"
                    ? structured.senderKind
                    : undefined
                }
              >
                <div
                  className={`flex max-w-[85%] flex-col gap-1.5 ${
                    isSystem ? "items-start" : "items-end"
                  }`}
                >
                  {parsed.attachments.length > 0 && (
                    <div className="flex flex-wrap justify-end gap-1">
                      {parsed.attachments.map((attachment, i) => (
                        <AttachmentPill key={i} label={attachment.label} />
                      ))}
                    </div>
                  )}
                  {parsed.text && (
                    <span
                      className={`rounded-lg px-3 py-2 text-[13px] whitespace-pre-wrap text-foreground ${
                        isSystem
                          ? "border border-border bg-muted/50"
                          : "bg-muted"
                      }`}
                    >
                      {parsed.text}
                    </span>
                  )}
                </div>
              </div>
            )
          })}
          {busy && (
            <div className="flex justify-start">
              <div className="px-3 py-2 text-xs text-muted-foreground">
                Thinking…
              </div>
            </div>
          )}
        </div>
      )}

      <div className="p-3">
        <div className="flex flex-col gap-1.5 rounded-2xl border border-border bg-background px-1.5 py-1.5 transition-colors focus-within:border-ring/60">
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-0.5 pl-2">
              {attachments.map((attachment) => (
                <AttachmentPill
                  key={attachment.id}
                  label={attachmentPillLabel(attachment)}
                  onRemove={() => removeAttachment(attachment.id)}
                />
              ))}
            </div>
          )}
          <div className="flex items-end gap-2 pl-2">
            <Textarea
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault()
                  submitComposer()
                }
              }}
              placeholder="Ask anything about this PR…"
              rows={1}
              className="max-h-40 min-h-7 flex-1 resize-none rounded-none border-0 bg-transparent px-0 py-1 shadow-none focus-visible:border-transparent focus-visible:ring-0 dark:bg-transparent"
            />
            <TooltipIconButton
              variant="default"
              size="icon"
              onClick={submitComposer}
              disabled={(!value.trim() && attachments.length === 0) || busy}
              label="Send message"
              className="rounded-full"
            >
              <ArrowUpIcon className="size-4" />
            </TooltipIconButton>
          </div>
        </div>
      </div>
    </div>
  )
}

function ChatPanel({
  owner,
  repo,
  number,
  assistantId,
  threadId,
}: {
  owner: string
  repo: string
  number: number
  assistantId: string
  threadId: string
}) {
  const client = useMemo(
    () => createDashboardClient(reviewChatApiBase(owner, repo, number)),
    [owner, repo, number]
  )

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <StreamProvider
        client={client}
        assistantId={assistantId}
        fetch={dashboardFetch}
        threadId={threadId}
      >
        <ChatBody />
      </StreamProvider>
    </div>
  )
}

export function ReviewChat({
  owner,
  repo,
  number,
}: {
  owner: string
  repo: string
  number: number
}) {
  const meta = useQuery({
    queryKey: ["review-chat", owner, repo, number],
    queryFn: () => api.getReviewChat(owner, repo, number),
  })

  if (meta.isPending) {
    return (
      <div className="flex flex-1 flex-col gap-3 p-4">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    )
  }

  if (meta.isError || !meta.data.available) {
    return (
      <Empty>
        <EmptyDescription>
          Chat is unavailable right now. Reload the page to try again.
        </EmptyDescription>
      </Empty>
    )
  }

  return (
    <ChatPanel
      key={`${owner}/${repo}/${number}`}
      owner={owner}
      repo={repo}
      number={number}
      assistantId={meta.data.assistant_id}
      threadId={meta.data.thread_id}
    />
  )
}
