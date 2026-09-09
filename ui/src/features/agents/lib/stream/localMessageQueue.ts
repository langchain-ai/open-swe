import type { Client } from "@langchain/langgraph-sdk"

import type { ImageChunk } from "@/features/agents/lib/types"
import { imageBlocks } from "./promptMessage"

/**
 * Mid-run follow-ups for a local thread. The agent's
 * `check_message_queue_before_model` hook drains this store item into the
 * running graph, so the shape here has to match what that hook reads.
 */
const QUEUE_KEY = "pending_messages"

interface QueuedPayload {
  text?: string
  images?: Array<{ base64?: string; mime_type?: string; file_name?: string }>
}

interface QueueItem {
  content: QueuedPayload
}

export interface QueuedPrompt {
  text: string
  images: Array<ImageChunk>
}

function namespace(sessionId: string): Array<string> {
  return ["queue", sessionId]
}

function payloadImages(payload: QueuedPayload): Array<ImageChunk> {
  return (payload.images ?? []).flatMap((block) =>
    block.base64 && block.mime_type
      ? [
          {
            kind: "image" as const,
            base64: block.base64,
            mimeType: block.mime_type,
            ...(block.file_name ? { fileName: block.file_name } : {}),
          },
        ]
      : []
  )
}

async function readQueue(
  client: Client,
  sessionId: string
): Promise<Array<QueueItem>> {
  const item = await client.store.getItem(namespace(sessionId), QUEUE_KEY)
  const messages = item?.value?.messages
  return Array.isArray(messages) ? (messages as Array<QueueItem>) : []
}

export async function enqueueLocalPrompt(
  client: Client,
  sessionId: string,
  prompt: QueuedPrompt,
  login: string | undefined
): Promise<void> {
  const pending = await readQueue(client, sessionId)
  await client.store.putItem(namespace(sessionId), QUEUE_KEY, {
    messages: [
      ...pending,
      {
        content: {
          text: prompt.text,
          images: imageBlocks(prompt.images),
          ...(login && {
            sender: {
              id: `github:${login}`,
              platform: "github",
              github_login: login,
            },
          }),
        },
      },
    ],
  })
}

/** Whatever the agent left undrained, merged into one prompt. */
export async function readLocalPromptQueue(
  client: Client,
  sessionId: string
): Promise<QueuedPrompt | null> {
  const pending = await readQueue(client, sessionId)
  if (pending.length === 0) return null
  const payloads = pending.map((item) => item.content ?? {})
  const text = payloads
    .map((payload) => payload.text?.trim())
    .filter(Boolean)
    .join("\n\n")
  const images = payloads.flatMap(payloadImages)
  return text || images.length > 0 ? { text, images } : null
}

export async function clearLocalPromptQueue(
  client: Client,
  sessionId: string
): Promise<void> {
  await client.store.deleteItem(namespace(sessionId), QUEUE_KEY)
}
