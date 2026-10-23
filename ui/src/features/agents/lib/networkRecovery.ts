import { AIMessage, HumanMessage } from "@langchain/core/messages"

import { parseStructuredInput } from "./structuredInputMessages"
import type { BaseMessage } from "@langchain/core/messages"
import type { DesktopRecoveryReason } from "@/desktop"

/** Sender of the hidden continuation runs the desktop coordinator dispatches. */
export const NETWORK_RESUME_SENDER = "system:network-resume"

const OFFLINE_MESSAGE_PATTERNS = [
  /fetch failed/i,
  /failed to fetch/i,
  /networkerror/i,
  /network error/i,
  /ENOTFOUND/,
  /ECONNRESET/,
  /ECONNREFUSED/,
  /ETIMEDOUT/,
  /EAI_AGAIN/,
]

export function isOfflineErrorMessage(message: string | null): boolean {
  return (
    typeof message === "string" &&
    OFFLINE_MESSAGE_PATTERNS.some((pattern) => pattern.test(message))
  )
}

export function streamErrorMessage(error: unknown): string | null {
  if (!error) return null
  if (error instanceof Error) return error.message
  return typeof error === "string" ? error : null
}

function streamErrorStatus(error: unknown): number | null {
  if (!error || typeof error !== "object") return null
  const status = (error as { status?: unknown }).status
  return typeof status === "number" ? status : null
}

/**
 * Whether a failed agent stream is worth continuing, and under which label.
 * A 4xx is the server answering — retrying would only repeat it. Everything
 * else while the app is online is a broken transport: the local backend is the
 * only hop, so the coordinator's probe decides whether it comes back.
 */
export function classifyStreamFailure({
  online,
  message,
  status,
}: {
  online: boolean
  message: string | null
  status: number | null
}): DesktopRecoveryReason | null {
  if (status !== null && status >= 400 && status < 500) return null
  if (!online) return "network"
  if (!message) return null
  return "network"
}

export function classifyStreamError(
  error: unknown,
  online: boolean
): DesktopRecoveryReason | null {
  return classifyStreamFailure({
    online,
    message: streamErrorMessage(error),
    status: streamErrorStatus(error),
  })
}

/**
 * A continuation the desktop dispatched for recovery. It is a real message in
 * the graph's history — the model has to see it — but never one the user wrote,
 * so the transcript drops it.
 */
export function isNetworkResumeMessage(message: BaseMessage): boolean {
  if (!HumanMessage.isInstance(message)) return false
  const parsed = parseStructuredInput(message.text)
  return parsed.type === "message" && parsed.sender === NETWORK_RESUME_SENDER
}

export interface NetworkRecoveryMerge {
  /** The history with every hidden continuation input removed. */
  messages: Array<BaseMessage>
  /**
   * Indexes in `messages` of the assistant messages that carry on from an
   * earlier attempt. Their text is joined with everything already in the turn
   * instead of replacing it, which is what makes a recovery read as one answer.
   */
  continued: Set<number>
}

/**
 * Drop the hidden continuation inputs so the assistant turns on either side of
 * them join into one answer. Attempts stay in state order, so `generation`
 * ordering and usage are already correct.
 */
export function mergeNetworkRecoveryAttempts(
  messages: ReadonlyArray<BaseMessage>
): NetworkRecoveryMerge {
  const visible: Array<BaseMessage> = []
  const continued = new Set<number>()
  let awaitingAttempt = false
  for (const message of messages) {
    if (isNetworkResumeMessage(message)) {
      awaitingAttempt = true
      continue
    }
    if (awaitingAttempt) {
      awaitingAttempt = false
      if (AIMessage.isInstance(message)) continued.add(visible.length)
    }
    visible.push(message)
  }
  return { messages: visible, continued }
}
