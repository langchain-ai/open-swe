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
import { getMessageContentString } from "@open-swe/shared/messages";
import { TaskPlan } from "@open-swe/shared/open-swe/types";
import { useThreadPolling } from "@/hooks/useThreadPolling";

export interface ThreadWithTasks extends Thread {
  threadTitle: string;
  repository: string;
  branch: string;
  completedTasksCount: number;
  totalTasksCount: number;
  tasks: TaskPlan | undefined;
  proposedPlan: string[];
}

interface ThreadContextType {
  threads: ThreadWithTasks[];
  setThreads: Dispatch<SetStateAction<ThreadWithTasks[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
  refreshThreads: () => Promise<void>;
  getThread: (threadId: string) => Promise<ThreadWithTasks | null>;
  selectedThread: ThreadWithTasks | null;
  setSelectedThread: (thread: ThreadWithTasks | null) => void;
  isPending: boolean;
  recentlyUpdatedThreads: Set<string>;
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
  // Default to existing counts to prevent zero-flashing during loading states
  const defaultCounts = existingCounts || {
    totalTasksCount: 0,
    completedTasksCount: 0,
  };

  // If we have proposed plans but no tasks yet (initial state)
  if (proposedPlan && proposedPlan.length > 0 && !tasks) {
    return {
      totalTasksCount: proposedPlan.length,
      completedTasksCount: 0,
    };
  }

  if (!tasks) {
    // No tasks passed, return 0s
    return defaultCounts;
  }

  const activeTaskList = tasks.tasks.find(
    (t) => t.taskIndex === tasks.activeTaskIndex,
  );
  if (!activeTaskList) {
    // Something is wrong here. Return 0
    return defaultCounts;
  }

  const activeTaskPlans = activeTaskList.planRevisions.find(
    (p) => p.revisionIndex === activeTaskList.activeRevisionIndex,
  );
  if (!activeTaskPlans) {
    // Something is wrong here. Return 0
    return defaultCounts;
  }

  return {
    totalTasksCount: activeTaskPlans.plans.length,
    completedTasksCount: activeTaskPlans.plans.filter((p) => p.completed)
      .length,
  };
};

export function ThreadProvider({ children }: { children: ReactNode }) {
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";

  const [threads, setThreads] = useState<ThreadWithTasks[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [selectedThread, setSelectedThread] = useState<ThreadWithTasks | null>(
    null,
  );
  const [isPending, startTransition] = useTransition();
  const [recentlyUpdatedThreads, setRecentlyUpdatedThreads] = useState<
    Set<string>
  >(new Set());

  const getThread = useCallback(
    async (threadId: string): Promise<ThreadWithTasks | null> => {
      if (!apiUrl || !assistantId) return null;
      const client = createClient(apiUrl);

      try {
        const thread = await client.threads.get(threadId);
        return enhanceThreadWithTasks(thread);
      } catch (error) {
        console.error("Failed to fetch thread:", threadId, error);
        return null;
      }
    },
    [apiUrl, assistantId],
  );

  const enhanceThreadWithTasks = (thread: Thread): ThreadWithTasks => {
    const threadValues = thread.values as any;
    const plan: TaskPlan | undefined = threadValues?.plan;
    const proposedPlan: string[] = threadValues?.proposedPlan || [];

    const targetRepository = threadValues?.targetRepository;
    const messages = (threadValues as any)?.messages;
    const firstMessageContent = messages?.[0]?.content;
    const threadTitle = firstMessageContent
      ? getMessageContentString(firstMessageContent)
      : `Thread ${thread.thread_id.substring(0, 8)}`;

    const { totalTasksCount, completedTasksCount } = getTaskCounts(
      plan,
      proposedPlan,
    );

    return {
      ...thread,
      threadTitle,
      repository:
        targetRepository?.repo ||
        targetRepository?.name ||
        "Unknown Repository",
      branch: targetRepository?.branch || "main",
      completedTasksCount,
      totalTasksCount,
      tasks: plan,
      proposedPlan,
    };
  };

  const refreshThreads = useCallback(async (): Promise<void> => {
    if (!apiUrl || !assistantId) return;

    setThreadsLoading(true);
    const client = createClient(apiUrl);

    try {
      // Simple thread search - try both metadata approaches
      const searchParams = {
        limit: 100,
        metadata: getThreadSearchMetadata(assistantId),
      };

      let threadsResponse = await client.threads.search(searchParams);

      // If no threads found, try alternative metadata
      if (threadsResponse.length === 0) {
        const altMetadata = assistantId.includes("-")
          ? { assistant_id: assistantId }
          : { graph_id: assistantId };
        threadsResponse = await client.threads.search({
          limit: 100,
          metadata: altMetadata,
        });
      }

      // Enhance threads with task data
      const enhancedThreads: ThreadWithTasks[] = [];
      for (const thread of threadsResponse) {
        try {
          const fullThread = await client.threads.get(thread.thread_id);
          enhancedThreads.push(enhanceThreadWithTasks(fullThread));
        } catch (error) {
          console.error(`Failed to enhance thread ${thread.thread_id}:`, error);
        }
      }

      // Sort by creation date (newest first)
      enhancedThreads.sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );

      setThreads(enhancedThreads);
    } catch (error) {
      console.error("Failed to fetch threads:", error);
    } finally {
      setThreadsLoading(false);
    }
  }, [apiUrl, assistantId]);

  // Now using polling-only approach for consistent cross-tab updates

  // Initial load
  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  // Clear selectedThread when navigating to different threads or away from threads
  useEffect(() => {
    return () => {
      // Cleanup selectedThread when ThreadProvider unmounts or context changes
      setSelectedThread(null);
    };
  }, []);

  // Polling callbacks
  const handlePollingUpdate = useCallback(
    (updatedThreads: ThreadWithTasks[], changedThreadIds: string[]) => {
      // Update threads state
      setThreads((currentThreads) => {
        const updatedMap = new Map(updatedThreads.map((t) => [t.thread_id, t]));
        return currentThreads.map(
          (thread) => updatedMap.get(thread.thread_id) || thread,
        );
      });

      // Mark threads as recently updated for animations
      setRecentlyUpdatedThreads(new Set(changedThreadIds));

      // Clear animation state after 1 second
      setTimeout(() => {
        setRecentlyUpdatedThreads(new Set());
      }, 1000);
    },
    [],
  );

  const handlePollComplete = useCallback(() => {
    if (process.env.NODE_ENV === "development") {
      console.log("🔄 Thread polling completed");
    }
  }, []);

  const handlePollError = useCallback((error: string) => {
    if (process.env.NODE_ENV === "development") {
      console.error("❌ Thread polling error:", error);
    }
  }, []);

  // Initialize polling
  useThreadPolling({
    threads,
    getThread,
    onUpdate: handlePollingUpdate,
    onPollComplete: handlePollComplete,
    onError: handlePollError,
    enabled: true, // Always enabled now that it's our primary update mechanism
  });

  const value = {
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
    refreshThreads,
    getThread,
    selectedThread,
    setSelectedThread,
    isPending,
    recentlyUpdatedThreads,
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
