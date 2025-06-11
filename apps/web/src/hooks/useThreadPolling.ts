import { useEffect, useRef } from "react";
import { ThreadPoller, PollConfig } from "@/lib/polling/thread-poller";
import { ThreadWithTasks } from "@/providers/Thread";

interface UseThreadPollingProps {
  threads: ThreadWithTasks[];
  getThread: (threadId: string) => Promise<ThreadWithTasks | null>;
  onUpdate: (
    updatedThreads: ThreadWithTasks[],
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
