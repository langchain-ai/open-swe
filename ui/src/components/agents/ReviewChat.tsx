import { useCallback, useEffect, useRef, useState } from "react"
import { StreamProvider, useStreamContext } from "@langchain/react"
import { overrideFetchImplementation } from "@langchain/langgraph-sdk"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowClockwiseIcon,
  ArrowUpIcon,
  PlusIcon,
  SparkleIcon,
  XIcon,
} from "@phosphor-icons/react"
import type { BaseMessage } from "@langchain/core/messages"

import { Markdown } from "@/components/agents/ported"
import { IconButton } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import { api, reviewChatApiBase } from "@/lib/api"
import { cn } from "@/lib/utils"

const dashboardFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: "include" })

// The SDK's internal Client issues some reads (getState, history) outside the
// transport's fetch; without this they drop the session cookie cross-origin.
overrideFetchImplementation(dashboardFetch)

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

function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-1 flex-col gap-4 p-4">
      <p className="text-sm text-foreground">
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
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-muted/60"
          >
            <SparkleIcon className="size-4 shrink-0 text-muted-foreground" />
            {prompt}
          </button>
        ))}
      </div>
    </div>
  )
}

function ChatBody() {
  const stream = useStreamContext()
  const [value, setValue] = useState("")
  const endRef = useRef<HTMLDivElement>(null)
  const messages = stream.messages
  const busy = stream.isLoading

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || busy) return
      void stream.submit({ messages: [{ type: "human", content: trimmed }] })
    },
    [busy, stream],
  )

  const visible = messages.filter((message) => {
    const type = messageType(message)
    if (type !== "human" && type !== "ai") return false
    return messageText(message.content).trim().length > 0
  })

  const submitComposer = () => {
    send(value)
    setValue("")
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {visible.length === 0 && !busy ? (
        <EmptyState onPick={send} />
      ) : (
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
          {visible.map((message, index) => {
            const isUser = messageType(message) === "human"
            return (
              <div
                key={message.id ?? index}
                className={cn("flex", isUser ? "justify-end" : "justify-start")}
              >
                <div
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                    isUser ? "bg-muted text-foreground" : "text-foreground",
                  )}
                >
                  {isUser ? (
                    <span className="whitespace-pre-wrap">
                      {messageText(message.content)}
                    </span>
                  ) : (
                    <Markdown content={messageText(message.content)} />
                  )}
                </div>
              </div>
            )
          })}
          {busy && (
            <div className="flex justify-start">
              <div className="px-3 py-2 text-sm text-muted-foreground">
                Thinking…
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
      )}

      <div className="border-t border-border p-3">
        <div className="flex items-end gap-2">
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
            className="max-h-40 min-h-9 flex-1 resize-none"
          />
          <IconButton
            type="button"
            onClick={submitComposer}
            disabled={!value.trim() || busy}
            aria-label="Send message"
          >
            <ArrowUpIcon className="size-4" />
          </IconButton>
        </div>
      </div>
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
  const qc = useQueryClient()
  const meta = useQuery({
    queryKey: ["review-chat", owner, repo, number],
    queryFn: () => api.getReviewChat(owner, repo, number),
  })
  const threadsQuery = useQuery({
    queryKey: ["review-chat-threads", owner, repo, number],
    queryFn: () => api.listReviewChatThreads(owner, repo, number),
    enabled: meta.data?.available === true,
  })

  const serverThreads = threadsQuery.data?.threads ?? []
  const initialDraftId = useRef(crypto.randomUUID())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const activeId =
    selectedId ?? serverThreads[0]?.thread_id ?? initialDraftId.current
  const isDraft = !serverThreads.some((t) => t.thread_id === activeId)

  const invalidateThreads = useCallback(() => {
    void qc.invalidateQueries({
      queryKey: ["review-chat-threads", owner, repo, number],
    })
  }, [qc, owner, repo, number])

  const deleteThread = useMutation({
    mutationFn: (id: string) =>
      api.deleteReviewChatThread(owner, repo, number, id),
    onSuccess: invalidateThreads,
  })

  const closeTab = useCallback(
    (id: string) => {
      const persisted = serverThreads.some((t) => t.thread_id === id)
      const remaining = serverThreads.filter((t) => t.thread_id !== id)
      if (persisted) deleteThread.mutate(id)
      if (id === activeId) {
        setSelectedId(remaining[0]?.thread_id ?? crypto.randomUUID())
      }
    },
    [serverThreads, activeId, deleteThread],
  )

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
      <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-muted-foreground">
        Chat becomes available once the review has finished running.
      </div>
    )
  }

  const tabs = isDraft
    ? [{ thread_id: activeId, title: "New chat" }, ...serverThreads]
    : serverThreads

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        <div className="flex flex-1 items-center gap-1 overflow-x-auto">
          {tabs.map((tab) => (
            <div
              key={tab.thread_id}
              className={cn(
                "flex shrink-0 items-center gap-1 rounded-md pl-2 pr-1 py-1 text-xs",
                tab.thread_id === activeId
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted/50",
              )}
            >
              <button
                type="button"
                onClick={() => setSelectedId(tab.thread_id)}
                className="max-w-[140px] truncate"
              >
                {tab.title}
              </button>
              <IconButton
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Close chat"
                onClick={() => closeTab(tab.thread_id)}
              >
                <XIcon />
              </IconButton>
            </div>
          ))}
        </div>
        <IconButton
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="New chat"
          onClick={() => setSelectedId(crypto.randomUUID())}
        >
          <PlusIcon />
        </IconButton>
        <IconButton
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh chats"
          onClick={() => void threadsQuery.refetch()}
        >
          <ArrowClockwiseIcon />
        </IconButton>
      </div>

      <StreamProvider
        key={activeId}
        apiUrl={reviewChatApiBase(owner, repo, number)}
        assistantId={meta.data.assistant_id}
        fetch={dashboardFetch}
        threadId={activeId}
        onCompleted={invalidateThreads}
      >
        <ChatBody />
      </StreamProvider>
    </div>
  )
}
