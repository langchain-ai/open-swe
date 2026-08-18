import type { Message } from "./types"

export function threadMessagesForDisplay({
  live,
  retained,
  optimistic,
  streamThreadId,
  threadId,
  isThreadLoading,
}: {
  live: Array<Message>
  retained: Array<Message>
  optimistic: Array<Message>
  streamThreadId: string | null | undefined
  threadId: string
  isThreadLoading: boolean
}): Array<Message> {
  if (live.length > 0) return live
  if (retained.length > 0 && (streamThreadId !== threadId || isThreadLoading)) {
    return retained
  }
  if (optimistic.length > 0) return optimistic
  return live
}
