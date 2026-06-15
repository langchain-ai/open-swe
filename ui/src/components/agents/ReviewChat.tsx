import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { StreamProvider, useStreamContext } from "@langchain/react"
import { overrideFetchImplementation } from "@langchain/langgraph-sdk"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowClockwiseIcon,
  ArrowUpIcon,
  CheckIcon,
  PlusIcon,
  SparkleIcon,
  TrashIcon,
  XIcon,
} from "@phosphor-icons/react"
import { Menu } from "@base-ui/react/menu"
import type { BaseMessage } from "@langchain/core/messages"

import type { ReviewChatThread } from "@/lib/api"
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

// Kept in sync with the backend's `_derive_title` so the optimistic tab label
// matches the title the server persists for the thread.
const DEFAULT_TITLE = "New chat"
const TITLE_MAX_CHARS = 60

function deriveTitle(text: string): string {
  const flattened = text.trim().split(/\s+/).join(" ")
  return flattened ? flattened.slice(0, TITLE_MAX_CHARS) : DEFAULT_TITLE
}

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

// --- Conversation store ------------------------------------------------------
//
// The client owns the conversation list. Each conversation is a client-minted
// thread id; the thread is created server-side lazily on its first message.
// The server thread list is only used to recover conversations on a fresh
// browser and to reconcile titles — it is never the sole source for the tabs,
// so an empty/lagging server response can no longer make open chats vanish.

interface Conversation {
  id: string
  title: string
  createdAt: number
}

interface ChatState {
  conversations: Array<Conversation>
  activeId: string
}

function storageKey(owner: string, repo: string, number: number): string {
  return `osw:review-chat:${owner}/${repo}/${number}`
}

function newDraft(): Conversation {
  return { id: crypto.randomUUID(), title: DEFAULT_TITLE, createdAt: Date.now() }
}

function isConversation(value: unknown): value is Conversation {
  if (!value || typeof value !== "object") return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.id === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.createdAt === "number"
  )
}

function loadState(key: string): ChatState {
  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(key)
      if (raw) {
        const parsed: unknown = JSON.parse(raw)
        const source = (parsed ?? {}) as Record<string, unknown>
        const conversations = Array.isArray(source.conversations)
          ? source.conversations.filter(isConversation)
          : []
        const first = conversations[0]
        if (first) {
          const activeId =
            typeof source.activeId === "string" &&
            conversations.some((c) => c.id === source.activeId)
              ? source.activeId
              : first.id
          return { conversations, activeId }
        }
      }
    } catch {
      /* fall through to a fresh draft */
    }
  }
  const draft = newDraft()
  return { conversations: [draft], activeId: draft.id }
}

function saveState(key: string, state: ChatState): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(key, JSON.stringify(state))
  } catch {
    /* ignore quota / availability errors */
  }
}

function useConversations(key: string) {
  const [state, setState] = useState<ChatState>(() => loadState(key))

  const update = useCallback(
    (fn: (prev: ChatState) => ChatState) => {
      setState((prev) => {
        const next = fn(prev)
        if (next === prev) return prev
        saveState(key, next)
        return next
      })
    },
    [key],
  )

  const select = useCallback(
    (conversation: Conversation) => {
      update((prev) => ({
        conversations: prev.conversations.some((c) => c.id === conversation.id)
          ? prev.conversations
          : [...prev.conversations, conversation],
        activeId: conversation.id,
      }))
    },
    [update],
  )

  const newChat = useCallback(() => {
    update((prev) => {
      const active = prev.conversations.find((c) => c.id === prev.activeId)
      // Reuse a pristine, never-sent draft instead of stacking empty tabs.
      if (active && active.title === DEFAULT_TITLE) return prev
      const draft = newDraft()
      return { conversations: [...prev.conversations, draft], activeId: draft.id }
    })
  }, [update])

  // Names a conversation from its first message; later messages don't rename it.
  const nameConversation = useCallback(
    (id: string, title: string) => {
      update((prev) => {
        const current = prev.conversations.find((c) => c.id === id)
        if (!current || current.title !== DEFAULT_TITLE) return prev
        return {
          ...prev,
          conversations: prev.conversations.map((c) =>
            c.id === id ? { ...c, title } : c,
          ),
        }
      })
    },
    [update],
  )

  const close = useCallback(
    (id: string) => {
      update((prev) => {
        const index = prev.conversations.findIndex((c) => c.id === id)
        if (index === -1) return prev
        const remaining = prev.conversations.filter((c) => c.id !== id)
        const fallback = remaining[Math.max(0, index - 1)]
        if (!fallback) {
          const draft = newDraft()
          return { conversations: [draft], activeId: draft.id }
        }
        return {
          conversations: remaining,
          activeId: prev.activeId === id ? fallback.id : prev.activeId,
        }
      })
    },
    [update],
  )

  return { ...state, select, newChat, nameConversation, close }
}

// --- View --------------------------------------------------------------------

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

function ChatBody({
  onUserSend,
  expectsHistory,
}: {
  onUserSend: (text: string) => void
  expectsHistory: boolean
}) {
  const stream = useStreamContext()
  const [value, setValue] = useState("")
  const endRef = useRef<HTMLDivElement>(null)
  const messages = stream.messages
  const busy = stream.isLoading
  // True during the one-time getState hydration when switching to / loading an
  // existing thread, before its messages have arrived.
  const hydrating = stream.isThreadLoading

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || busy) return
      onUserSend(trimmed)
      void stream.submit({ messages: [{ type: "human", content: trimmed }] })
    },
    [busy, stream, onUserSend],
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

  // Show the loading placeholder (not the empty/intro state) while an existing
  // conversation hydrates, so a chat with messages never flashes its greeting.
  const showEmpty = visible.length === 0 && !busy
  const showLoading = showEmpty && hydrating && expectsHistory

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {showLoading ? (
        <LoadingState />
      ) : showEmpty ? (
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
                    "text-sm text-foreground",
                    isUser
                      ? "max-w-[85%] rounded-lg bg-muted px-3 py-2"
                      : "w-full",
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

      <div className="p-3">
        <div className="flex items-end gap-2 rounded-2xl border border-border bg-background py-1.5 pl-3.5 pr-1.5 transition-colors focus-within:border-ring/60">
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
            className="max-h-40 min-h-7 flex-1 resize-none rounded-none border-0 bg-transparent px-0 py-1 shadow-none focus-visible:border-transparent focus-visible:ring-0"
          />
          <IconButton
            type="button"
            onClick={submitComposer}
            disabled={!value.trim() || busy}
            aria-label="Send message"
            className="rounded-full"
          >
            <ArrowUpIcon className="size-4" />
          </IconButton>
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
}: {
  owner: string
  repo: string
  number: number
  assistantId: string
}) {
  const qc = useQueryClient()
  const threadsKey = useMemo(
    () => ["review-chat-threads", owner, repo, number] as const,
    [owner, repo, number],
  )
  const { conversations, activeId, select, newChat, nameConversation, close } =
    useConversations(storageKey(owner, repo, number))

  const threadsQuery = useQuery({
    queryKey: threadsKey,
    queryFn: () => api.listReviewChatThreads(owner, repo, number),
  })
  const serverThreads = useMemo(
    () => threadsQuery.data?.threads ?? [],
    [threadsQuery.data],
  )

  const invalidateThreads = useCallback(() => {
    void qc.invalidateQueries({ queryKey: threadsKey })
  }, [qc, threadsKey])

  const deleteThread = useMutation({
    mutationFn: (id: string) =>
      api.deleteReviewChatThread(owner, repo, number, id),
    onMutate: (id: string) => {
      qc.setQueryData<{ threads: Array<ReviewChatThread> }>(threadsKey, (old) =>
        old ? { threads: old.threads.filter((t) => t.thread_id !== id) } : old,
      )
    },
    onSettled: invalidateThreads,
  })

  // Open tabs are the client's conversations (titles reconciled from the
  // server). The history dropdown is the full set — open tabs plus every other
  // conversation the server knows about (e.g. recovered on a fresh browser) —
  // newest first, so past chats are reachable without cluttering the tab strip.
  const { openTabs, historyItems } = useMemo(() => {
    const byId = new Map(conversations.map((c) => [c.id, { ...c }]))
    for (const thread of serverThreads) {
      const existing = byId.get(thread.thread_id)
      if (existing) {
        if (thread.title && thread.title !== DEFAULT_TITLE) {
          existing.title = thread.title
        }
      } else {
        byId.set(thread.thread_id, {
          id: thread.thread_id,
          title: thread.title || DEFAULT_TITLE,
          createdAt: thread.updated_at ? Date.parse(thread.updated_at) || 0 : 0,
        })
      }
    }
    return {
      openTabs: conversations.map((c) => byId.get(c.id) ?? c),
      historyItems: [...byId.values()].sort((a, b) => b.createdAt - a.createdAt),
    }
  }, [conversations, serverThreads])

  const handleClose = useCallback(
    (id: string) => {
      if (serverThreads.some((t) => t.thread_id === id)) {
        deleteThread.mutate(id)
      }
      close(id)
    },
    [serverThreads, deleteThread, close],
  )

  const handleUserSend = useCallback(
    (text: string) => {
      nameConversation(activeId, deriveTitle(text))
    },
    [nameConversation, activeId],
  )

  // Whether the active conversation should already have messages: it exists
  // server-side, or it's been named by a sent message. Drives the hydration
  // placeholder so an existing chat never flashes the intro state on load.
  const expectsHistory = useMemo(() => {
    if (serverThreads.some((t) => t.thread_id === activeId)) return true
    const active = conversations.find((c) => c.id === activeId)
    return active !== undefined && active.title !== DEFAULT_TITLE
  }, [serverThreads, conversations, activeId])

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
        <div className="flex flex-1 items-center gap-1 overflow-x-auto">
          {openTabs.map((tab) => (
            <div
              key={tab.id}
              className={cn(
                "flex shrink-0 items-center gap-1 rounded-md py-1 pl-2 pr-1 text-xs",
                tab.id === activeId
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted/50",
              )}
            >
              <button
                type="button"
                onClick={() => select(tab)}
                className="max-w-[140px] truncate"
              >
                {tab.title}
              </button>
              <IconButton
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Close chat"
                onClick={() => handleClose(tab.id)}
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
          onClick={newChat}
        >
          <PlusIcon />
        </IconButton>
        <Menu.Root
          onOpenChange={(open) => {
            if (open) void threadsQuery.refetch()
          }}
        >
          <Menu.Trigger
            render={
              <IconButton
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Chat history"
              >
                <ArrowClockwiseIcon />
              </IconButton>
            }
          />
          <Menu.Portal>
            <Menu.Positioner align="end" sideOffset={6} className="z-50">
              <Menu.Popup className="origin-(--transform-origin) max-h-80 w-64 overflow-y-auto rounded-lg bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10 outline-none">
                {historyItems.length === 0 ? (
                  <div className="px-2 py-1.5 text-xs text-muted-foreground">
                    No conversations yet
                  </div>
                ) : (
                  historyItems.map((item) => (
                    <div
                      key={item.id}
                      className="group/hist relative flex items-stretch"
                    >
                      <Menu.Item
                        onClick={() => select(item)}
                        className="flex flex-1 cursor-default items-center gap-2 rounded-md py-1.5 pl-2 pr-8 text-xs outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground"
                      >
                        {item.id === activeId ? (
                          <CheckIcon className="size-3.5 shrink-0" />
                        ) : (
                          <span className="size-3.5 shrink-0" />
                        )}
                        <span className="flex-1 truncate text-left">
                          {item.title}
                        </span>
                      </Menu.Item>
                      <IconButton
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        aria-label="Delete chat"
                        className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover/hist:opacity-100"
                        onClick={() => handleClose(item.id)}
                      >
                        <TrashIcon />
                      </IconButton>
                    </div>
                  ))
                )}
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>

      <StreamProvider
        key={activeId}
        apiUrl={reviewChatApiBase(owner, repo, number)}
        assistantId={assistantId}
        fetch={dashboardFetch}
        threadId={activeId}
        onCompleted={invalidateThreads}
      >
        <ChatBody onUserSend={handleUserSend} expectsHistory={expectsHistory} />
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
      <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-muted-foreground">
        Chat becomes available once the review has finished running.
      </div>
    )
  }

  return (
    <ChatPanel
      key={`${owner}/${repo}/${number}`}
      owner={owner}
      repo={repo}
      number={number}
      assistantId={meta.data.assistant_id}
    />
  )
}
