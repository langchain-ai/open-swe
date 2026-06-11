import { useEffect, useRef, useState } from "react";

import type { Message } from "@/lib/agents/types";

/** How long run-idle must persist before clearing the live markdown target. */
const LIVE_MARKDOWN_CLEAR_MS = 2000;

/**
 * Latches the agent message id that should stay in Streamdown "live" mode.
 * `stream.isLoading` flickers on lifecycle events between graph steps; this
 * keeps markdown streaming until the run has been idle for a beat.
 */
export function useLiveMarkdownMessageId(
  visibleMessages: Message[],
  streamIsLoading: boolean | undefined,
  isStreaming: boolean,
): string | null {
  const [liveMessageId, setLiveMessageId] = useState<string | null>(null);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const runActive = Boolean(streamIsLoading || isStreaming);

  useEffect(() => {
    if (runActive) {
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
        clearTimerRef.current = undefined;
      }

      const lastMessage = visibleMessages[visibleMessages.length - 1];
      // A new user prompt at the tail means the prior agent turn is done — keep it
      // static so Streamdown does not re-animate it while waiting for the reply.
      if (!lastMessage || lastMessage.author === "user") {
        setLiveMessageId(null);
        return;
      }

      if (lastMessage.author === "agent") {
        setLiveMessageId((prev) => (prev === lastMessage.id ? prev : lastMessage.id));
      }
      return;
    }

    clearTimerRef.current = setTimeout(() => {
      clearTimerRef.current = undefined;
      setLiveMessageId(null);
    }, LIVE_MARKDOWN_CLEAR_MS);

    return () => {
      if (clearTimerRef.current) {
        clearTimeout(clearTimerRef.current);
        clearTimerRef.current = undefined;
      }
    };
  }, [runActive, visibleMessages]);

  return liveMessageId;
}
