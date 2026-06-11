import { useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

import type { PendingPrompt } from "@/lib/agents/pendingPrompts"
import type {
  AgentThread,
  ImageChunk,
  Message,
} from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/provider/useModelOptions"
import { AgentGitPanel } from "@/components/agents/AgentGitPanel"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { Messages } from "@/components/agents/messages"
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

function sameImages(a: Array<ImageChunk> = [], b: Array<ImageChunk> = []) {
  if (a.length !== b.length) return false
  return a.every((image, i) => {
    const other = b[i]
    return (
      other?.base64 === image.base64 &&
      other.mimeType === image.mimeType &&
      (other.fileName ?? null) === (image.fileName ?? null)
    )
  })
}

function samePendingPrompt(a: PendingPrompt, b: PendingPrompt) {
  return (
    a.prompt === b.prompt &&
    a.insertAt === b.insertAt &&
    sameImages(a.images, b.images) &&
    (a.modelId ?? null) === (b.modelId ?? null) &&
    (a.effort ?? null) === (b.effort ?? null)
  )
}

function userMessageMatchesPending(message: Message, entry: PendingPrompt) {
  if (message.author !== "user") return false
  const text = message.chunks
    .flatMap((chunk) => (chunk.kind === "text" ? [chunk.text] : []))
    .join("")
  const images = message.chunks.flatMap((chunk) =>
    chunk.kind === "image" ? [chunk] : []
  )
  return text === entry.prompt && sameImages(images, entry.images)
}

function isPendingPromptConfirmed(messages: Array<Message>, entry: PendingPrompt) {
  return messages.some((message) => userMessageMatchesPending(message, entry))
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
      .then(() => {
        setPendingPrompts((prev) => {
          const next = dropPendingPrompts(thread.id, (candidate) =>
            samePendingPrompt(candidate, entry)
          )
          return next.length === prev.length ? prev : next
        })
        pendingSubmitStarted.current = false
      })
      .catch(() => {
        // Leave the pending entry in place, but do not automatically resubmit it.
      })
  }, [
    pendingPrompts,
    activeSelection?.effort,
    activeSelection?.modelId,
    stream,
    stream.isLoading,
    stream.isThreadLoading,
    thread.id,
  ])

  const baseMessages = useMemo<Array<Message>>(() => {
    const live = streamMessagesToUi(stream.messages, stream.toolCalls, stream.subagents)
    if (live.length > 0 || stream.isLoading) return live
    return thread.messages
  }, [
    stream.isLoading,
    stream.messages,
    stream.toolCalls,
    stream.subagents,
    thread.messages,
  ])

  useEffect(() => {
    setPendingPrompts((prev) => {
      if (prev.length === 0) return prev
      const next = dropPendingPrompts(thread.id, (entry) =>
        isPendingPromptConfirmed(baseMessages, entry)
      )
      return next.length === prev.length ? prev : next
    })
  }, [baseMessages, thread.id])

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
  // The transcript hydrates from the SDK (`GET …/state` → `stream.messages`).
  // Show a loading state during that one-time fetch instead of the empty state.
  const isHydrating = stream.isThreadLoading && !hasMessages

  return (
    <div className="flex min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        {hasMessages ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <Messages
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
      <AgentGitPanel thread={thread} />
    </div>
  )
}
