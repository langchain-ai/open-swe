import { createClient } from "@/providers/client";
import { Thread } from "@langchain/langgraph-sdk";
import { useCallback, useEffect, useState } from "react";

export function useThreads<State extends Record<string, any>>(
  assistantId?: string,
) {
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const [threads, setThreads] = useState<Thread<State>[] | null>(null);
  const [threadsLoading, setThreadsLoading] = useState(false);

  const getThread = useCallback(
    async (threadId: string): Promise<Thread<State> | null> => {
      if (!apiUrl) return null;
      const client = createClient(apiUrl);

      try {
        const thread = await client.threads.get<State>(threadId);
        return thread;
      } catch (error) {
        console.error("Failed to fetch thread:", threadId, error);
        return null;
      }
    },
    [apiUrl],
  );

  const getThreads = useCallback(async (): Promise<Thread<State>[] | null> => {
    if (!apiUrl) return null;
    setThreadsLoading(true);
    const client = createClient(apiUrl);

    try {
      const searchArgs = assistantId
        ? {
            metadata: {
              graph_id: assistantId,
            },
          }
        : undefined;
      const threads = await client.threads.search<State>(searchArgs);
      return threads;
    } catch (error) {
      console.error("Failed to fetch threads:", error);
      return null;
    } finally {
      setThreadsLoading(false);
    } 
  }, [apiUrl]);

  useEffect(() => {
    getThreads().then((threads) => {
      setThreads(threads);
    });
  }, [getThreads]);

  return { threads, setThreads, getThread, getThreads, threadsLoading };
}
