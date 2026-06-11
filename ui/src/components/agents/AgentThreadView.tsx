import { useMemo, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

import type { AgentThread, Message } from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/provider/useModelOptions"
import { AgentGitPanel } from "@/components/agents/AgentGitPanel"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { Messages } from "@/components/agents/messages"
import { streamMessagesToUi } from "@/lib/agents/streamMessagesToUi"
import { useSubmitAgentMessage } from "@/lib/agents/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/lib/agents/provider/useModelOptions"

interface AgentThreadViewProps {
  thread: AgentThread
}

// The stream lives at the `/agents` layout (one persistent provider that
// survives the home → thread navigation), so this view only consumes it.
export function AgentThreadView({ thread }: AgentThreadViewProps) {
  const sendMessage = useSubmitAgentMessage(thread.id)
  const stream = useAgentThreadStream()

  const { models, defaultSelection } = useModelOptions()
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!thread.model || !thread.effort) return null
    const supported = models.some(
      (m) => m.id === thread.model && m.efforts.includes(thread.effort ?? "")
    )
    if (!supported) return null
    return { modelId: thread.model, effort: thread.effort }
  }, [models, thread.model, thread.effort])
  const [selection, setSelection] = useState<ModelSelection | null>(null)
  const activeSelection = selection ?? threadSelection ?? defaultSelection

  const baseMessages = useMemo<Array<Message>>(() => {
    const live = streamMessagesToUi(stream.messages, stream.toolCalls, stream.subagents)
    if (live.length > 0) return live
    // Optimistic transcript seeded by `AgentsHome` on thread creation (the
    // only case where a fetched thread carries messages — `getThread` returns
    // none). Bridges the brief gap before the SDK's optimistic `submit` echo
    // lands in `stream.messages`.
    if (thread.messages.length > 0) return thread.messages
    return live
  }, [
    stream.messages,
    stream.toolCalls,
    stream.subagents,
    thread.messages,
  ])

  const hasMessages = baseMessages.length > 0
  const isStreaming = thread.status === "running" || stream.isLoading
  const isThinking = stream.isLoading
  const settingUpSandbox = isThinking && baseMessages.length === 0
  // The transcript hydrates from the SDK (`GET …/state` → `stream.messages`).
  // Show a loading state during that one-time fetch instead of the empty state.
  const isHydrating = stream.isThreadLoading && !hasMessages

  return (
    <div className="flex min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {hasMessages ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <Messages
              messages={baseMessages}
              isStreaming={isStreaming}
              streamIsLoading={stream.isLoading}
              isThinking={isThinking}
              settingUpSandbox={settingUpSandbox}
              contentWidthClass="max-w-3xl"
            />
            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full min-w-0 max-w-3xl">
                <AgentPromptBar
                  placeholder="Add a follow up"
                  compact
                  busy={isStreaming}
                  disabled={sendMessage.isPending}
                  onSubmit={(content, images) =>
                    sendMessage.mutate({
                      content,
                      images,
                      model_id: activeSelection?.modelId ?? null,
                      effort: activeSelection?.effort ?? null,
                    })
                  }
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelection}
                />
              </div>
            </div>
          </div>
        ) : isHydrating ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            <p className="text-sm text-[var(--ui-text-dim)]">Loading conversation…</p>
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6">
            <p className="text-sm text-[var(--ui-text-dim)]">
              This thread has no messages yet.
            </p>
            <div className="w-full max-w-3xl">
              <AgentPromptBar
                placeholder="Send the first message"
                compact
                busy={isStreaming}
                disabled={sendMessage.isPending}
                onSubmit={(content, images) =>
                  sendMessage.mutate({
                    content,
                    images,
                    model_id: activeSelection?.modelId ?? null,
                    effort: activeSelection?.effort ?? null,
                  })
                }
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
              />
            </div>
          </div>
        )}
      </div>
      <AgentGitPanel thread={thread} messages={baseMessages} />
    </div>
  )
}
