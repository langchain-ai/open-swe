import { useEffect, useRef } from "react";
import { ThreadPoller, PollConfig } from "@/lib/polling/thread-poller";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { Thread } from "@langchain/langgraph-sdk";

interface UseThreadPollingProps {
  threads: Thread<GraphState>[];
  getThread: (threadId: string) => Promise<Thread<GraphState> | null>;
  onUpdate: (
    updatedThreads: Thread<GraphState>[],
    changedThreadIds: string[],
  ) => void;

  enabled?: boolean;
}

export function useThreadPolling({
  threads,
  getThread,
  onUpdate,
  enabled = true,
}: UseThreadPollingProps) {
  const pollerRef = useRef<ThreadPoller | null>(null);

  useEffect(() => {
    if (!enabled) return;

    const config: PollConfig = {
      interval: 15000,
      onUpdate,
    };

    pollerRef.current = new ThreadPoller(config, threads, getThread);
    pollerRef.current.start();

    return () => {
      if (pollerRef.current) {
        pollerRef.current.stop();
        pollerRef.current = null;
      }
    };
  }, [threads, getThread, onUpdate, enabled]);

  return {
    start: () => pollerRef.current?.start(),
    stop: () => pollerRef.current?.stop(),
  };
}
