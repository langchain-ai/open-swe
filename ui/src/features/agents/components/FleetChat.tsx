import { useMemo, useState } from "react"
import { coerceMessageLikeToMessage } from "@langchain/core/messages"
import { useStream } from "@langchain/langgraph-sdk/react"
import type { Message } from "@langchain/langgraph-sdk"
import { useQueryClient } from "@tanstack/react-query"
import { Banner } from "@langchain/macaw-components/Banner"
import { AgentChatEmptyState } from "@/features/agents/components/AgentChatEmptyState"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import {
  ChatComposer,
  type ComposerSubmission,
  type RestoredDraft,
} from "@/features/agents/components/composer/ChatComposer"
import { Messages } from "@/features/agents/components/messages"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import {
  fleetClient,
  FLEET_ASSISTANT_ID,
  FLEET_THREADS_KEY,
} from "@/features/agents/lib/fleetChat"

export function FleetChat({
  threadId,
  onThreadCreated,
}: {
  threadId?: string
  onThreadCreated: (threadId: string) => void
}) {
  const cache = useQueryClient()
  const client = useMemo(() => fleetClient(), [])
  const stream = useStream<{ messages: Message[] }>({
    client,
    assistantId: FLEET_ASSISTANT_ID,
    threadId: threadId ?? null,
    fetchStateHistory: false,
    reconnectOnMount: true,
    onThreadId: onThreadCreated,
    onFinish: () => {
      void cache.invalidateQueries({ queryKey: FLEET_THREADS_KEY })
    },
  })
  const [addedReply, setAddedReply] =
    useState<Parameters<typeof ChatComposer>[0]["addedReply"]>(null)
  const [restoreDraft, setRestoreDraft] = useState<RestoredDraft | null>(null)
  const messages = streamMessagesToUi(
    stream.messages.map((message) => coerceMessageLikeToMessage(message))
  )

  async function send({ text, images, replies }: ComposerSubmission) {
    if (stream.isLoading || stream.interrupt) return
    const content = [
      ...replies.map(
        (reply) =>
          `Reply to ${reply.source}${reply.file ? ` (${reply.file})` : ""}:\n${reply.before}${reply.selection}${reply.after}\n\n${reply.comment}`
      ),
      text,
    ]
      .filter(Boolean)
      .join("\n\n")
    const message: Message = {
      id: crypto.randomUUID(),
      type: "human",
      content: images.length
        ? [
            { type: "text", text: content },
            ...images.map((image) => ({
              type: "image_url" as const,
              image_url: {
                url: `data:${image.mimeType};base64,${image.base64}`,
              },
            })),
          ]
        : content,
    }
    await stream.submit(
      { messages: [message] },
      {
        metadata: { assistant_id: FLEET_ASSISTANT_ID },
        multitaskStrategy: "reject",
        streamResumable: true,
        onError: () =>
          setRestoreDraft({ key: Date.now(), text, images, replies }),
        optimisticValues: (previous) => ({
          messages: [...(previous.messages ?? []), message],
        }),
      }
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <AgentThreadHeader target="Cloud" panelCollapsed />
      {messages.length || threadId ? (
        <Messages
          messages={messages}
          scrollKey={threadId}
          isStreaming={stream.isLoading}
          streamIsLoading={stream.isLoading}
          contentWidthClass="max-w-3xl"
          onReply={(reply) => setAddedReply({ key: Date.now(), reply })}
        />
      ) : (
        <AgentChatEmptyState />
      )}
      <AgentComposerDock>
        {Boolean(stream.error) && (
          <Banner intent="error" className="mb-space-3 w-full">
            {stream.error instanceof Error
              ? stream.error.message
              : "Fleet could not complete the request."}
          </Banner>
        )}
        {stream.interrupt && (
          <Banner intent="warning" className="mb-space-3 w-full">
            Fleet needs your input. Open this conversation in LangSmith to
            continue.
          </Banner>
        )}
        <ChatComposer
          autoFocus
          compact
          placeholder="Do anything"
          onSubmit={send}
          activeRun={
            stream.isLoading
              ? { threadId: threadId ?? "", running: true }
              : undefined
          }
          busy={stream.isLoading}
          disabled={stream.isLoading || Boolean(stream.interrupt)}
          onStop={() => stream.stop()}
          addedReply={addedReply}
          restoreDraft={restoreDraft}
        />
      </AgentComposerDock>
    </div>
  )
}
