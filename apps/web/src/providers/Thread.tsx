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
  useTransition,
} from "react";
import { createClient } from "./client";
import { TaskPlan, GraphState } from "@open-swe/shared/open-swe/types";
import { useThreadPolling } from "@/hooks/useThreadPolling";

interface ThreadContextType {
  threads: Thread<GraphState>[];
  setThreads: Dispatch<SetStateAction<Thread<GraphState>[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
  refreshThreads: () => Promise<void>;
  getThread: (threadId: string) => Promise<Thread<GraphState> | null>;
  isPending: boolean;
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

const getTaskCounts = (
  tasks?: TaskPlan,
  proposedPlan?: string[],
  existingCounts?: { totalTasksCount: number; completedTasksCount: number },
): { totalTasksCount: number; completedTasksCount: number } => {
  const defaultCounts = existingCounts || {
    totalTasksCount: 0,
    completedTasksCount: 0,
  };

  if (proposedPlan && proposedPlan.length > 0 && !tasks) {
    return {
      totalTasksCount: proposedPlan.length,
      completedTasksCount: 0,
    };
  }

  if (!tasks || !tasks.tasks || tasks.tasks.length === 0) {
    return defaultCounts;
  }
  const activeTaskIndex = tasks.activeTaskIndex;
  const activeTask = tasks.tasks.find(
    (task) => task.taskIndex === activeTaskIndex,
  );

  if (
    !activeTask ||
    !activeTask.planRevisions ||
    activeTask.planRevisions.length === 0
  ) {
    return defaultCounts;
  }

  const activeRevisionIndex = activeTask.activeRevisionIndex;
  const activeRevision = activeTask.planRevisions.find(
    (revision) => revision.revisionIndex === activeRevisionIndex,
  );

  if (
    !activeRevision ||
    !activeRevision.plans ||
    activeRevision.plans.length === 0
  ) {
    return defaultCounts;
  }

  const plans = activeRevision.plans;

  const completedTasksCount = plans.filter((p) => p.completed)?.length || 0;

  return {
    totalTasksCount: plans.length,
    completedTasksCount,
  };
};

export function ThreadProvider({ children }: { children: ReactNode }) {
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";

  const [threads, setThreads] = useState<Thread<GraphState>[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [isPending, startTransition] = useTransition();
  const [recentlyUpdatedThreads, setRecentlyUpdatedThreads] = useState<
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

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  const handlePollingUpdate = useCallback(
    (updatedThreads: Thread<GraphState>[], changedThreadIds: string[]) => {
      setThreads((currentThreads) => {
        const updatedMap = new Map(updatedThreads.map((t) => [t.thread_id, t]));
        return currentThreads.map(
          (thread) => updatedMap.get(thread.thread_id) || thread,
        );
      });

      setRecentlyUpdatedThreads(new Set(changedThreadIds));

      setTimeout(() => {
        setRecentlyUpdatedThreads(new Set());
      }, 2000);
    },
    [],
  );

  // Initialize polling
  useThreadPolling({
    threads,
    getThread,
    onUpdate: handlePollingUpdate,
    enabled: true,
  });

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
    isPending,
    recentlyUpdatedThreads,
    handleThreadClick,
  };

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}

export function useThreads() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreads must be used within a ThreadProvider");
  }
  return context;
}
