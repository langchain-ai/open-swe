import { useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { AppendMessage, QueueItemState } from "@assistant-ui/react"
import type { UseStreamRuntimeOptions } from "@assistant-ui/react-langchain"
import { AgentsApiError, agentsApi } from "@/features/agents/lib/api"
import type { ThreadMessageRequest } from "@/features/agents/lib/api"
import type { AgentThread } from "@/features/agents/lib/types"
import { threadKey } from "./threadListAdapter"

type QueueFactory = NonNullable<UseStreamRuntimeOptions["createQueueAdapter"]>
type PendingSend = { id: string; message: AppendMessage; error?: string }

export function queueRequest(
  message: AppendMessage,
  id: string
): ThreadMessageRequest {
  const config = message.runConfig?.custom
  const content = message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
  const images = [
    ...message.content,
    ...(message.attachments ?? []).flatMap((attachment) => attachment.content),
  ]
    .filter((part) => part.type === "image")
    .map((part) => {
      const match = /^data:(image\/(?:png|jpeg|gif|webp));base64,(.+)$/s.exec(
        part.image
      )
      if (!match?.[1] || !match[2])
        throw new Error("Use PNG, JPEG, GIF, or WebP images.")
      return {
        kind: "image" as const,
        mimeType: match[1],
        base64: match[2],
        fileName: part.filename,
      }
    })
  return {
    content,
    images,
    client_message_id: id,
    model_id:
      typeof config?.agent_model_id === "string" ? config.agent_model_id : null,
    effort:
      typeof config?.agent_effort === "string" ? config.agent_effort : null,
    plan_mode: config?.plan_mode === true,
  }
}

function pendingItem(entry: PendingSend): QueueItemState {
  const parts = entry.message.content.flatMap((part) => {
    if (part.type === "text" || part.type === "file") return [part]
    return []
  })
  return {
    id: entry.id,
    prompt: parts
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n"),
    parts,
  }
}

export function useServerQueue(
  threadId: string | undefined,
  thread: AgentThread | undefined
) {
  const client = useQueryClient()
  const [pending, setPending] = useState<PendingSend[]>([])
  const requests = useRef(new Set<string>())
  const remove = (id: string) =>
    setPending((items) => items.filter((item) => item.id !== id))

  const createQueueAdapter: QueueFactory = ({ send, stream }) => {
    const persistedIds = new Set(stream.messages.map((message) => message.id))
    const serverItems: QueueItemState[] = (thread?.queuedMessages ?? [])
      .filter((message) => !persistedIds.has(message.id))
      .map((message) => ({
        id: message.id,
        prompt: message.content,
        parts: [
          { type: "text", text: message.content },
          ...(message.images ?? []).map((image) => ({
            type: "file" as const,
            data: `data:${image.mimeType};base64,${image.base64}`,
            mimeType: image.mimeType,
            filename: image.fileName,
          })),
        ],
      }))
    const serverIds = new Set(serverItems.map((item) => item.id))
    const items = [
      ...serverItems,
      ...pending
        .filter(
          (entry) => !persistedIds.has(entry.id) && !serverIds.has(entry.id)
        )
        .map(pendingItem),
    ]

    const submit = (
      message: AppendMessage,
      id: string = crypto.randomUUID()
    ) => {
      if (requests.current.has(id)) return
      requests.current.add(id)
      const entry: PendingSend = { id, message }
      setPending((previous) => [
        ...previous.filter((item) => item.id !== id),
        entry,
      ])
      void (async () => {
        const request = queueRequest(message, id)
        if (request.content.trim() === "/offload") {
          if (!threadId || !thread)
            throw new Error("Offloading requires an existing conversation.")
          if (stream.isLoading || thread.status === "running")
            throw new Error(
              "Wait for the current run to finish before offloading."
            )
          if (
            message.attachments?.length ||
            message.content.some((part) => part.type !== "text")
          )
            throw new Error("Offloading does not accept attachments.")
          remove(id)
          await stream.submit(
            {},
            {
              config: { configurable: { offload_conversation: true } },
            }
          )
          return
        }
        if (threadId && thread) {
          try {
            const accepted = await agentsApi.queueMessage(threadId, request)
            await client.cancelQueries({ queryKey: threadKey(threadId) })
            // The acceptance response is a summary without the server queue.
            client.setQueryData<AgentThread>(
              threadKey(threadId),
              (current) => ({
                ...current,
                ...accepted,
                queuedMessages: [
                  ...(current?.queuedMessages ?? []).filter(
                    (item) => item.id !== id
                  ),
                  {
                    id,
                    content: request.content,
                    images: request.images,
                    createdAt: Date.now(),
                  },
                ],
              })
            )
            remove(id)
            void client.invalidateQueries({ queryKey: threadKey(threadId) })
            return
          } catch (error) {
            if (!(error instanceof AgentsApiError) || error.status !== 409)
              throw error
          }
        }
        remove(id)
        await send(message)
      })()
        .catch((error: unknown) => {
          setPending((previous) => [
            ...previous.filter((item) => item.id !== id),
            {
              ...entry,
              error:
                error instanceof Error
                  ? error.message
                  : "Could not send this message.",
            },
          ])
        })
        .finally(() => requests.current.delete(id))
    }

    return {
      items,
      steerItems: [],
      enqueue: submit,
      steer: submit,
      move(id) {
        const failed = pending.find((item) => item.id === id && item.error)
        if (!failed)
          throw new Error("The server does not support moving queued messages.")
        submit(failed.message, id)
      },
      edit() {
        throw new Error("The server does not support editing queued messages.")
      },
      remove(id) {
        if (!pending.some((item) => item.id === id && item.error))
          throw new Error(
            "The server does not support removing accepted messages."
          )
        remove(id)
      },
    }
  }

  return {
    createQueueAdapter,
    queueErrors: Object.fromEntries(
      pending.filter((item) => item.error).map((item) => [item.id, item.error])
    ),
  }
}
