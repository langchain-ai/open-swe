"use client";

import { Thread } from "@langchain/langgraph-sdk";
import { ReactNode, useCallback, useState, useEffect } from "react";
import { createClient } from "./client";
import { GraphState } from "@openswe/shared/open-swe/types";
import { ThreadContext, ThreadContextType } from "./thread-context";
import {
  getAlternateThreadSearchMetadata,
  getThreadSearchMetadata,
} from "@/lib/thread";

export function ThreadProvider({ children }: { children: ReactNode }) {
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";

  const [threads, setThreads] = useState<Thread<GraphState>[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [recentlyUpdatedThreads, _setRecentlyUpdatedThreads] = useState<
    Set<string>
  >(new Set());

  const getThread = useCallback(
    async (threadId: string): Promise<Thread<GraphState> | null> => {
      if (!apiUrl || !assistantId) return null;
      const client = createClient(apiUrl);

      try {
        const thread = await client.threads.get<GraphState>(threadId);
        return thread;
      } catch (error) {
        console.error("Failed to fetch thread:", threadId, error);
        return null;
      }
    },
    [apiUrl, assistantId],
  );

  const refreshThreads = useCallback(async (): Promise<void> => {
    if (!apiUrl || !assistantId) return;

    setThreadsLoading(true);
    const client = createClient(apiUrl);

    try {
      const searchParams = {
        limit: 100,
        metadata: getThreadSearchMetadata(assistantId),
      };

      let threadsResponse =
        await client.threads.search<GraphState>(searchParams);

      if (threadsResponse.length === 0) {
        threadsResponse = await client.threads.search<GraphState>({
          limit: 100,
          metadata: getAlternateThreadSearchMetadata(assistantId),
        });
      }

      threadsResponse.sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );

      setThreads(threadsResponse);
    } catch (error) {
      console.error("Failed to fetch threads:", error);
    } finally {
      setThreadsLoading(false);
    }
  }, [apiUrl, assistantId]);

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  const handleThreadClick = useCallback(
    (
      thread: Thread<GraphState>,
      currentThreadId: string | null,
      setThreadId: (id: string) => void,
    ) => {
      if (currentThreadId === thread.thread_id) return;

      setThreadId(thread.thread_id);
    },
    [],
  );

  const value = {
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
    refreshThreads,
    getThread,
    recentlyUpdatedThreads,
    handleThreadClick,
  } as ThreadContextType;

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}
