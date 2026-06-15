import { useCallback, useEffect, useRef, useState } from "react"
import { StreamProvider, useStreamContext } from "@langchain/react"
import { overrideFetchImplementation } from "@langchain/langgraph-sdk"
import { useQuery } from "@tanstack/react-query"
import { ArrowUpIcon } from "@phosphor-icons/react"
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

function ChatMessages() {
  const stream = useStreamContext()
  const endRef = useRef<HTMLDivElement>(null)
  const messages = stream.messages

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const visible = messages.filter((message) => {
    const type = messageType(message)
    if (type !== "human" && type !== "ai") return false
    return messageText(message.content).trim().length > 0
  })

  if (visible.length === 0 && !stream.isLoading) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-1 p-6 text-center text-xs text-muted-foreground">
        <p className="font-medium text-foreground">Chat about this PR</p>
        <p>
          Ask about the diff, the findings, or the surrounding code. The
          assistant has read-only access to the repository.
        </p>
      </div>
    )
  }

  return (
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
                isUser
                  ? "bg-muted text-foreground"
                  : "text-foreground",
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
      {stream.isLoading && (
        <div className="flex justify-start">
          <div className="px-3 py-2 text-sm text-muted-foreground">
            Thinking…
          </div>
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}

function ChatComposer() {
  const stream = useStreamContext()
  const [value, setValue] = useState("")
  const busy = stream.isLoading

  const send = useCallback(() => {
    const text = value.trim()
    if (!text || busy) return
    setValue("")
    void stream.submit({ messages: [{ type: "human", content: text }] })
  }, [value, busy, stream])

  return (
    <div className="border-t border-border p-3">
      <div className="flex items-end gap-2">
        <Textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault()
              send()
            }
          }}
          placeholder="Ask anything about this PR…"
          rows={1}
          className="max-h-40 min-h-9 flex-1 resize-none"
        />
        <IconButton
          type="button"
          onClick={send}
          disabled={!value.trim() || busy}
          aria-label="Send message"
        >
          <ArrowUpIcon className="size-4" />
        </IconButton>
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
      <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-muted-foreground">
        Chat becomes available once the review has finished running.
      </div>
    )
  }

  return (
    <StreamProvider
      apiUrl={reviewChatApiBase(owner, repo, number)}
      assistantId={meta.data.assistant_id}
      fetch={dashboardFetch}
      threadId={meta.data.thread_id}
    >
      <div className="flex h-full flex-1 flex-col overflow-hidden">
        <ChatMessages />
        <ChatComposer />
      </div>
    </StreamProvider>
  )
}
