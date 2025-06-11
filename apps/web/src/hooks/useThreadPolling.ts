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
  onPollComplete: () => void;
  onError: (error: string) => void;
  enabled?: boolean;
}

export function useThreadPolling({
  threads,
  getThread,
  onUpdate,
  onPollComplete,
  onError,
  enabled = true,
}: UseThreadPollingProps) {
  const pollerRef = useRef<ThreadPoller | null>(null);

  useEffect(() => {
    if (!enabled) return;

    const config: PollConfig = {
      interval: 2000, // 2 seconds hardcoded
      onUpdate,
      onPollComplete,
      onError,
    };

    const getThreadsFn = () => threads;

    pollerRef.current = new ThreadPoller(config, getThreadsFn, getThread);
    pollerRef.current.start();

    return () => {
      if (pollerRef.current) {
        pollerRef.current.stop();
        pollerRef.current = null;
      }
    };
  }, [threads, getThread, onUpdate, onPollComplete, onError, enabled]);

  return {
    start: () => pollerRef.current?.start(),
    stop: () => pollerRef.current?.stop(),
  };
}
