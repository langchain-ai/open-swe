import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { agentsApi } from "./api";
import { agentThreadKeys } from "./queries";

export function useAgentThreadStream(threadId: string, enabled: boolean) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    let cancelled = false;

    async function consume() {
      try {
        const res = await fetch(agentsApi.streamUrl(threadId), {
          credentials: "include",
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            for (const line of block.split("\n")) {
              if (line.startsWith("data: ")) {
                queryClient.invalidateQueries({ queryKey: agentThreadKeys.detail(threadId) });
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.debug("agent thread stream closed", error);
        }
      }
    }

    void consume();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [enabled, queryClient, threadId]);
}
