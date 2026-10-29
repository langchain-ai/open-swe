import { useMemo, useState } from "react"
import { useStream } from "@langchain/langgraph-sdk/react"
import type { Message } from "@langchain/langgraph-sdk"
import { useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { cn } from "@/lib/utils"
import {
  fleetClient,
  FLEET_ASSISTANT_ID,
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
  const [error, setError] = useState<string | null>(null)
  const client = useMemo(() => fleetClient(workspaceId ?? ""), [workspaceId])
  const stream = useStream<{ messages: Message[] }>({
    client,
    assistantId: FLEET_ASSISTANT_ID,
    threadId: threadId ?? null,
    fetchStateHistory: false,
    reconnectOnMount: true,
    onThreadId: (id) => onSelect(workspaceId, id),
    onFinish: () => {
      void cache.invalidateQueries({ queryKey: ["fleet-threads", workspaceId] })
    },
    onError: (cause) =>
      setError(
        cause instanceof Error
          ? cause.message
          : "Fleet could not complete the reply."
      ),
  })
  const active = stream.isLoading
  const problem =
    error ??
    (stream.error instanceof Error
      ? stream.error.message
      : stream.error
        ? "Fleet could not load this conversation."
        : null)

  async function send() {
    if (!workspaceId || !draft.trim() || active || stream.interrupt) return
    const text = draft
    const message: Message = {
      id: crypto.randomUUID(),
      type: "human",
      content: text,
    }
    setDraft("")
    setError(null)
    await stream.submit(
      { messages: [message] },
      {
        metadata: { assistant_id: FLEET_ASSISTANT_ID },
        multitaskStrategy: "reject",
        streamResumable: true,
        optimisticValues: (previous) => ({
          messages: [...(previous.messages ?? []), message],
        }),
        onError: () => setDraft(text),
      }
    )
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
          <Button
            size="sm"
            color="secondary"
            onClick={() => {
              setError(null)
              void cache.invalidateQueries({ queryKey: ["fleet-workspaces"] })
              window.location.reload()
            }}
          >
            Retry
          </Button>
        </div>
      )}
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
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
                  : "Choose a LangSmith workspace to start. Connect your LangSmith workspace to start."}
              </p>
            </div>
          )}
          {stream.isThreadLoading && (
            <p role="status" className="text-sm text-secondary">
              Loading conversation…
            </p>
          )}
          <div className="mx-auto flex max-w-3xl flex-col gap-space-6">
            {stream.messages.map((message, index) => {
              const role = message.type
              if (role !== "human" && role !== "ai") return null
              const content =
                typeof message.content === "string"
                  ? message.content
                  : message.content
                      .filter((part) => part.type === "text")
                      .map((part) => part.text ?? "")
                      .join("\n")
              if (!content) return null
              const human = role === "human"
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
                    <p className="break-words whitespace-pre-wrap">{content}</p>
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
            {Boolean(stream.interrupt) && (
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
            disabled={!workspaceId || active}
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
            {active ? (
              <Button
                size="md"
                color="secondary"
                onClick={() =>
                  void stream
                    .stop()
                    .catch((cause: unknown) =>
                      setError(
                        cause instanceof Error
                          ? cause.message
                          : "Could not stop the reply."
                      )
                    )
                }
              >
                Stop
              </Button>
            ) : (
              <Button
                size="md"
                type="submit"
                disabled={
                  !workspaceId ||
                  !draft.trim() ||
                  active ||
                  Boolean(stream.interrupt)
                }
                loading={active}
              >
                Send
              </Button>
            )}
          </div>
        </form>
      </main>
    </div>
  )
}
