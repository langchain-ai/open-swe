import { useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

import type { PendingPrompt } from "@/lib/agents/pendingPrompts"
import type { AgentThread, Message } from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/provider/useModelOptions"
import { AgentGitPanel } from "@/components/agents/AgentGitPanel"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { MessageView } from "@/components/agents/ported"
import { AgentThreadStreamProvider } from "@/lib/agents/AgentThreadStreamProvider"
import { streamMessagesToUi } from "@/lib/agents/streamMessagesToUi"
import {
  dropPendingPrompts,
  getPendingPrompts,
} from "@/lib/agents/pendingPrompts"
import {
  submitAgentPrompt,
  useSubmitAgentMessage,
} from "@/lib/agents/provider/useSubmitAgentMessage"
import { useModelOptions } from "@/lib/agents/provider/useModelOptions"

interface AgentThreadViewProps {
  thread: AgentThread
}

export function AgentThreadView({ thread }: AgentThreadViewProps) {
  return (
    <AgentThreadStreamProvider threadId={thread.id}>
      <AgentThreadViewContent thread={thread} />
    </AgentThreadStreamProvider>
  )
}

function AgentThreadViewContent({ thread }: AgentThreadViewProps) {
  const sendMessage = useSubmitAgentMessage(thread.id)
  const stream = useAgentThreadStream()
  const [pendingPrompts, setPendingPrompts] = useState<Array<PendingPrompt>>(
    () => getPendingPrompts(thread.id)
  )
  const pendingSubmitStarted = useRef(false)

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

  useEffect(() => {
    if (pendingPrompts.length === 0) return
    if (stream.isLoading || stream.isThreadLoading) return
    if (pendingSubmitStarted.current) return

    const entry = pendingPrompts[0]
    if (!entry) return
    pendingSubmitStarted.current = true
    void submitAgentPrompt(stream, {
      content: entry.prompt,
      images: entry.images,
      model_id: entry.modelId ?? activeSelection?.modelId ?? null,
      effort: entry.effort ?? activeSelection?.effort ?? null,
    })
      .catch(() => {
        pendingSubmitStarted.current = false
      })
      .finally(() => {
        pendingSubmitStarted.current = false
      })
  }, [
    pendingPrompts,
    activeSelection?.effort,
    activeSelection?.modelId,
    stream,
    stream.isLoading,
    stream.isThreadLoading,
  ])

  const baseMessages = useMemo<Array<Message>>(() => {
    const live = streamMessagesToUi(stream.messages)
    if (live.length > 0 || stream.isLoading) return live
    return thread.messages
  }, [stream.isLoading, stream.messages, thread.messages])

  const userMessageTexts = useMemo(() => {
    return new Set(
      baseMessages
        .filter((m) => m.author === "user")
        .map((m) =>
          m.chunks.flatMap((c) => (c.kind === "text" ? [c.text] : [])).join("")
        )
    )
  }, [baseMessages])

  useEffect(() => {
    setPendingPrompts((prev) => {
      if (prev.length === 0) return prev
      const next = dropPendingPrompts(thread.id, (entry) =>
        userMessageTexts.has(entry.prompt)
      )
      return next.length === prev.length ? prev : next
    })
  }, [thread.id, userMessageTexts])

  const displayMessages = useMemo<Array<Message>>(() => {
    if (pendingPrompts.length === 0) return baseMessages

    const baseTimestamp = new Date().toISOString()
    const result = baseMessages.slice()
    pendingPrompts.forEach((entry, i) => {
      const chunks: Message["chunks"] = [...(entry.images ?? [])]
      if (entry.prompt) chunks.push({ kind: "text", text: entry.prompt })
      const synth: Message = {
        id: `pending-user-${i}`,
        author: "user",
        timestamp: baseTimestamp,
        chunks,
      }
      const at = Math.min(Math.max(entry.insertAt, 0), result.length)
      result.splice(at, 0, synth)
    })
    return result
  }, [baseMessages, pendingPrompts])

  const hasMessages = displayMessages.length > 0
  const isStreaming =
    thread.status === "running" || stream.isLoading || pendingPrompts.length > 0
  const isThinking = stream.isLoading || pendingPrompts.length > 0
  const settingUpSandbox = isThinking && baseMessages.length === 0

  return (
    <div className="flex min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {hasMessages ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <MessageView
              messages={displayMessages}
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
      <AgentGitPanel thread={thread} />
    </div>
  )
}
