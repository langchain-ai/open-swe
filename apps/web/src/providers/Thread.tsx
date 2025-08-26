import { validate } from "uuid";
import { Thread } from "@langchain/langgraph-sdk";
import {
  createContext,
  useContext,
  ReactNode,
  useCallback,
  useState,
  Dispatch,
  SetStateAction,
  useEffect,
} from "react";
import { createClient } from "./client";
import { GraphState } from "@open-swe/shared/agent-mojo/types";

interface ThreadContextType {
  threads: Thread<GraphState>[];
  setThreads: Dispatch<SetStateAction<Thread<GraphState>[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
  refreshThreads: () => Promise<void>;
  getThread: (threadId: string) => Promise<Thread<GraphState> | null>;
  deleteThread: (threadId: string) => Promise<boolean>;
  recentlyUpdatedThreads: Set<string>;
  handleThreadClick: (
    thread: Thread<GraphState>,
    currentThreadId: string | null,
    setThreadId: (id: string) => void,
  ) => void;
}

const ThreadContext = createContext<ThreadContextType | undefined>(undefined);

function getThreadSearchMetadata(
  assistantId: string,
): { graph_id: string } | { assistant_id: string } {
  if (validate(assistantId)) {
    return { assistant_id: assistantId };
  } else {
    return { graph_id: assistantId };
  }
}

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
        const altMetadata = assistantId.includes("-")
          ? { assistant_id: assistantId }
          : { graph_id: assistantId };
        threadsResponse = await client.threads.search<GraphState>({
          limit: 100,
          metadata: altMetadata,
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

  const deleteThread = useCallback(
    async (threadId: string): Promise<boolean> => {
      if (!apiUrl) return false;
      try {
        const client = createClient(apiUrl);
        await client.threads.delete(threadId);
        // Optimistically update local state
        setThreads((prev) => prev.filter((t) => t.thread_id !== threadId));
        return true;
      } catch (error) {
        console.error("Failed to delete thread:", threadId, error);
        // Ensure we refetch in case local state is stale
        refreshThreads();
        return false;
      }
    },
    [apiUrl, refreshThreads],
  );

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
  deleteThread,
    recentlyUpdatedThreads,
    handleThreadClick,
  };

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useThreadsContext() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreadsContext must be used within a ThreadProvider");
  }
  return context;
}
