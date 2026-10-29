import { useEffect, useState } from "react"
import { Client } from "@langchain/langgraph-sdk"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import { cn } from "@/lib/utils"

import {
  fleetRequest as request,
  type Thread,
} from "@/features/agents/lib/fleetChat"

export function FleetChat({
  workspaceId,
  threadId,
  onSelect,
}: {
  workspaceId?: string
  threadId?: string
  onSelect: (workspace?: string, thread?: string) => void
}) {
  const cache = useQueryClient()
  const [draft, setDraft] = useState("")
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [run, setRun] = useState<{ thread: string; id: string } | null>(null)
  const thread = useQuery({
    queryKey: ["fleet-thread", workspaceId, threadId],
    queryFn: () => request<Thread>(`/${workspaceId}/threads/${threadId}`),
    enabled: Boolean(workspaceId && threadId),
    retry: false,
  })
  const active =
    thread.data?.status === "busy" || thread.data?.status === "pending"
  const problem = error ?? thread.error?.message
  const messages = thread.data?.values?.messages ?? []

  useEffect(() => {
    if (!workspaceId || !threadId || run?.thread !== threadId) return
    const controller = new AbortController()
    const runId = run.id
    const client = new Client({
      apiUrl: new URL(
        dashboardApiUrl(`/fleet-chat/${workspaceId}`),
        window.location.origin
      ).href,
    })
    async function subscribe() {
      try {
        for await (const event of client.runs.joinStream(threadId, runId, {
          signal: controller.signal,
          streamMode: "values",
        })) {
          if (event.event === "error")
            throw new Error(
              "Fleet couldn’t finish this reply. Reopen the chat to retry."
            )
          if (event.event === "values") {
            const values: unknown = event.data
            if (
              values &&
              typeof values === "object" &&
              "messages" in values &&
              Array.isArray(values.messages)
            ) {
              cache.setQueryData<Thread>(
                ["fleet-thread", workspaceId, threadId],
                (previous) =>
                  previous
                    ? {
                        ...previous,
                        status: "busy",
                        values: values as Thread["values"],
                      }
                    : previous
              )
            }
          }
        }
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Connection to Fleet was lost. Reopen the chat."
          )
      } finally {
        if (!controller.signal.aborted) {
          setRun(null)
          void cache.invalidateQueries({
            queryKey: ["fleet-thread", workspaceId, threadId],
          })
          void cache.invalidateQueries({
            queryKey: ["fleet-threads", workspaceId],
          })
        }
      }
    }
    void subscribe()
    return () => controller.abort()
  }, [workspaceId, threadId, run, cache])

  async function send() {
    if (!workspaceId || !draft.trim() || sending || active) return
    const text = draft
    setDraft("")
    setSending(true)
    setError(null)
    const workspace = workspaceId
    let id = threadId
    try {
      if (!id) {
        const created = await request<Thread>(`/${workspace}/threads`, {})
        id = created.thread_id
        onSelect(workspace, id)
      }
      const key = ["fleet-thread", workspace, id]
      const previous = cache.getQueryData<Thread>(key)
      cache.setQueryData<Thread>(key, {
        ...previous,
        thread_id: id,
        status: "busy",
        values: {
          messages: [
            ...(previous?.values?.messages ?? []),
            { role: "user", content: text },
          ],
        },
      })
      try {
        const started = await request<{ run_id: string }>(
          `/${workspace}/threads/${id}/messages`,
          { text }
        )
        setRun({ thread: id, id: started.run_id })
      } catch (cause) {
        cache.setQueryData(key, previous)
        throw cause
      }
      await cache.invalidateQueries({
        queryKey: ["fleet-thread", workspace, id],
      })
      await cache.invalidateQueries({ queryKey: ["fleet-threads", workspace] })
    } catch (cause) {
      setDraft(text)
      setError(
        cause instanceof Error ? cause.message : "Message could not be sent."
      )
    } finally {
      setSending(false)
    }
  }

  async function stop() {
    if (!workspaceId || !run || run.thread !== threadId) return
    try {
      await request(
        `/${workspaceId}/threads/${threadId}/runs/${run.id}/cancel`,
        {}
      )
      await cache.invalidateQueries({
        queryKey: ["fleet-thread", workspaceId, threadId],
      })
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not stop the reply."
      )
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-level-1">
      <header className="flex flex-wrap items-center justify-between gap-space-3 border-b border-default px-space-6 py-space-4">
        <div>
          <h1 className="text-lg font-semibold text-primary">Chat</h1>
          <p className="text-sm text-secondary">
            Fleet’s general-purpose agent
          </p>
        </div>
      </header>
      {problem && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-space-3 border-b border-default bg-error px-space-6 py-space-3 text-sm text-primary"
        >
          {problem}
          <Link to="/my-settings/connections" className="underline">
            Connections
          </Link>
          <Button
            size="sm"
            color="secondary"
            onClick={() => {
              setError(null)
              void cache.invalidateQueries({ queryKey: ["fleet-workspaces"] })
              if (threadId) void thread.refetch()
            }}
          >
            Retry
          </Button>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <div
            className="flex-1 overflow-y-auto px-space-6 py-space-6"
            aria-label="Conversation"
            aria-live="polite"
          >
            {!threadId && (
              <div className="mx-auto flex h-full max-w-xl flex-col items-center justify-center gap-space-4 text-center">
                <ChatCircleIcon
                  size={36}
                  weight="regular"
                  className="text-icon-secondary"
                />
                <h2 className="text-xl font-semibold text-primary">
                  What can I help with?
                </h2>
                <p className="text-sm text-secondary">
                  Ask questions, research a topic, or work through an idea with
                  Fleet.
                </p>
                <p className="text-sm text-secondary">
                  {workspaceId
                    ? "This conversation uses your LangSmith workspace and account."
                    : "Choose a LangSmith workspace to start. Connect your account in Settings → Connections."}
                </p>
              </div>
            )}
            {thread.isLoading && (
              <p role="status" className="text-sm text-secondary">
                Loading conversation…
              </p>
            )}
            <div className="mx-auto flex max-w-3xl flex-col gap-space-6">
              {messages.map((message, index) => {
                const role = message.role ?? message.type
                if (!["user", "human", "assistant", "ai"].includes(role ?? ""))
                  return null
                const content =
                  typeof message.content === "string"
                    ? message.content
                    : message.content
                        .filter((part) => part.type === "text")
                        .map((part) => part.text ?? "")
                        .join("\n")
                if (!content) return null
                const human = role === "user" || role === "human"
                return (
                  <article
                    key={index}
                    className={cn(
                      "rounded-lg p-space-4 text-primary",
                      human && "bg-surface-level-2"
                    )}
                  >
                    <p className="mb-space-2 text-sm font-medium text-secondary">
                      {human ? "You" : "Fleet"}
                    </p>
                    {human ? (
                      <p className="break-words whitespace-pre-wrap">
                        {content}
                      </p>
                    ) : (
                      <Markdown content={content} />
                    )}
                  </article>
                )
              })}
              {active && (
                <p role="status" className="text-sm text-secondary">
                  Fleet is working…
                </p>
              )}
              {thread.data?.status === "error" && (
                <p role="alert" className="text-error text-sm">
                  Fleet couldn’t finish this reply. Try sending another message.
                </p>
              )}
              {thread.data?.status === "interrupted" && (
                <p role="status" className="text-sm text-secondary">
                  Fleet needs your input. Open this conversation in LangSmith to
                  continue.
                </p>
              )}
            </div>
          </div>
          <form
            className="mx-auto flex w-full max-w-3xl flex-col gap-space-3 px-space-6 pb-space-6"
            onSubmit={(event) => {
              event.preventDefault()
              void send()
            }}
          >
            <Textarea
              aria-label="Message Fleet"
              size="md"
              rows={3}
              resize="none"
              maxLength={32000}
              value={draft}
              onChange={setDraft}
              disabled={!workspaceId || sending}
              placeholder="Message Fleet…"
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault()
                  void send()
                }
              }}
            />
            <div className="flex items-center justify-between gap-space-3">
              <span className="text-xs text-secondary">
                Powered by LangSmith Fleet
              </span>
              {active && run?.thread === threadId ? (
                <Button size="md" color="secondary" onClick={() => void stop()}>
                  Stop
                </Button>
              ) : (
                <Button
                  size="md"
                  type="submit"
                  disabled={
                    !workspaceId ||
                    !draft.trim() ||
                    sending ||
                    active ||
                    thread.data?.status === "interrupted"
                  }
                  loading={sending}
                >
                  Send
                </Button>
              )}
            </div>
          </form>
        </main>
      </div>
    </div>
  )
}
