import { ensureMessageInstances } from "@langchain/langgraph-sdk/ui"

import { messageArrivalTimestamp } from "./messageTimestamps"
import { streamMessagesToUi } from "./streamMessagesToUi"
import type { BaseMessage } from "@langchain/core/messages"
import type { Message, ThreadTranscript } from "./types"

/**
 * Render the server's transcript snapshot through the same pipeline the live
 * stream uses.
 *
 * The snapshot exists so an opened thread paints immediately instead of
 * waiting on the SDK's `getState`. Running it through `streamMessagesToUi`
 * rather than converting server-side is what keeps the two paths from
 * drifting: there is one definition of how a message becomes a chunk, and the
 * snapshot inherits every fix made for the live path — including treating
 * message content as untrusted data that the React tree escapes.
 */
export function transcriptToUiMessages(
  transcript: ThreadTranscript | undefined
): Array<Message> {
  if (!transcript?.available || transcript.messages.length === 0) return []
  try {
    const revived = ensureMessageInstances(
      transcript.messages as Parameters<typeof ensureMessageInstances>[0]
    ) as Array<BaseMessage>
    // No assembled tool calls: those are a live-stream concept, and the
    // snapshot's tool results are already in the messages themselves.
    return streamMessagesToUi(revived, [], messageArrivalTimestamp)
  } catch {
    // A shape the coercion cannot read costs the fast paint, not the thread —
    // the SDK hydrate is still on its way.
    return []
  }
}
