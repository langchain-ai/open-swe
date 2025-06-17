import { createClient } from "@/providers/client";
import { Thread } from "@langchain/langgraph-sdk";
import { useCallback, useEffect, useState } from "react";

export function useThreads<State extends Record<string, any>>() {
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";
  const [threads, setThreads] = useState<Thread<State>[] | null>(null);

  const getThread = useCallback(
    async (threadId: string): Promise<Thread<State> | null> => {
      if (!apiUrl || !assistantId) return null;
      const client = createClient(apiUrl);

      try {
        const thread = await client.threads.get<State>(threadId);
        return thread;
      } catch (error) {
        console.error("Failed to fetch thread:", threadId, error);
        return null;
      }
    },
    [apiUrl, assistantId],
  );

  const getThreads = useCallback(async (): Promise<Thread<State>[] | null> => {
    if (!apiUrl || !assistantId) return null;
    const client = createClient(apiUrl);

    try {
      const threads = await client.threads.search<State>();
      return threads;
    } catch (error) {
      console.error("Failed to fetch threads:", error);
      return null;
    }
  }, [apiUrl, assistantId]);

  useEffect(() => {
    getThreads().then((threads) => {
      setThreads(threads);
    });
  }, [getThreads]);

  return { threads, setThreads, getThread, getThreads };
}
