import { useCallback, useEffect, useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import type { PendingPrompt } from "@/lib/agents/pendingPrompts"
import type { AgentThread, ImageChunk, Message } from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/useModelOptions"
import { AgentPromptBar } from "@/components/agents/AgentPromptBar"
import { MessageView } from "@/components/agents/ported"
import { agentThreadKeys, useSendAgentMessage } from "@/lib/agents/queries"
import {
  dropPendingPrompts,
  getPendingPrompts,
} from "@/lib/agents/pendingPrompts"
import { useAgentThreadStream } from "@/lib/agents/useThreadStream"
import { useModelOptions } from "@/lib/agents/useModelOptions"

interface AgentThreadViewProps {
  thread: AgentThread
}

function messageText(message: Message): string {
  return message.chunks
    .filter((chunk) => chunk.kind === "text")
    .map((chunk) => chunk.text)
    .join("")
}

function messageImageKey(message: Message): string {
  return message.chunks
    .filter((chunk) => chunk.kind === "image")
    .map((chunk) => `${chunk.mimeType}:${chunk.base64}`)
    .join("\u0000")
}

function pendingImageKey(entry: PendingPrompt): string {
  return (entry.images ?? [])
    .map((image) => `${image.mimeType}:${image.base64}`)
    .join("\u0000")
}

function isPendingPromptConfirmed(
  entry: PendingPrompt,
  messages: Array<Message>
): boolean {
  return messages.slice(entry.insertAt).some((message) => {
    if (message.author !== "user") return false
    return (
      messageText(message) === entry.prompt &&
      messageImageKey(message) === pendingImageKey(entry)
    )
  })
}

export function AgentThreadView({ thread }: AgentThreadViewProps) {
  const queryClient = useQueryClient()
  const sendMessage = useSendAgentMessage(thread.id)
  useAgentThreadStream(thread.id, thread.status === "running")
  const [pendingPrompts, setPendingPrompts] = useState<Array<PendingPrompt>>(
    () => getPendingPrompts(thread.id)
  )

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
    setPendingPrompts((prev) => {
      if (prev.length === 0) return prev
      const next = dropPendingPrompts(thread.id, (entry) =>
        isPendingPromptConfirmed(entry, thread.messages)
      )
      return next.length === prev.length ? prev : next
    })
  }, [thread.id, thread.messages])

  useEffect(() => {
    queryClient.setQueryData<Array<AgentThread> | undefined>(
      agentThreadKeys.all,
      (threads) =>
        threads?.map((item) =>
          item.id === thread.id
            ? { ...item, ...thread, messages: item.messages }
            : item
        )
    )
  }, [queryClient, thread])

  const handleSubmit = useCallback(
    (content: string, images: Array<ImageChunk>) => {
      setPendingPrompts((prev) => [
        ...prev,
        { prompt: content, insertAt: thread.messages.length + prev.length, images },
      ])
      sendMessage.mutate({
        content,
        images,
        model_id: activeSelection?.modelId ?? null,
        effort: activeSelection?.effort ?? null,
      })
    },
    [sendMessage, thread.messages.length, activeSelection]
  )

  const displayMessages = useMemo<Array<Message>>(() => {
    if (pendingPrompts.length === 0) return thread.messages
    const baseTimestamp = new Date().toISOString()
    const result = thread.messages.slice()
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
  }, [thread.messages, pendingPrompts])

  const hasMessages = displayMessages.length > 0
  const hasActiveRun = thread.status === "running"
  const isStreaming = hasActiveRun || pendingPrompts.length > 0
  const settingUpSandbox =
    hasActiveRun && thread.messages.length === 0 && pendingPrompts.length > 0

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col">
        {hasMessages ? (
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
            <MessageView
              messages={displayMessages}
              isStreaming={isStreaming}
              settingUpSandbox={settingUpSandbox}
              contentWidthClass="max-w-3xl"
            />
            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full max-w-3xl min-w-0">
                <AgentPromptBar
                  placeholder="Add a follow up"
                  compact
                  busy={isStreaming}
                  disabled={sendMessage.isPending}
                  onSubmit={handleSubmit}
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
                onSubmit={handleSubmit}
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
