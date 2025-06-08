import { validate } from "uuid";
import { getApiKey } from "@/lib/api-key";
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
import { getMessageContentString } from "@open-swe/shared/messages";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

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
  updateThreadFromStream: (threadId: string, streamValues: any) => void;
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
): { totalTasksCount: number; completedTasksCount: number } => {
  if (!tasks) {
    // No tasks passed, return 0s
    return { totalTasksCount: 0, completedTasksCount: 0 };
  }
  const activeTaskList = tasks.tasks.find(
    (t) => t.taskIndex === tasks.activeTaskIndex,
  );
  if (!activeTaskList) {
    // Something is wrong here. Return 0
    return { totalTasksCount: 0, completedTasksCount: 0 };
  }
  const activeTaskPlans = activeTaskList.planRevisions.find(
    (p) => p.revisionIndex === activeTaskList.activeRevisionIndex,
  );
  if (!activeTaskPlans) {
    // Something is wrong here. Return 0
    return { totalTasksCount: 0, completedTasksCount: 0 };
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

  // Real-time thread updater for all properties (replaces polling)
  const updateThreadFromStream = useCallback(
    (threadId: string, streamValues: any) => {
      if (!threadId || !streamValues) return;

      setThreads((currentThreads) => {
        const targetThread = currentThreads.find(
          (t) => t.thread_id === threadId,
        );
        if (!targetThread) return currentThreads; // Thread not found, no update needed

        const plan: TaskPlan | undefined = streamValues?.plan;
        const proposedPlan: string[] = streamValues?.proposedPlan || [];
        const targetRepository = streamValues?.targetRepository;
        const messages = streamValues?.messages;

        const { totalTasksCount, completedTasksCount } = getTaskCounts(plan);

        // Extract thread title from messages if available
        const firstMessageContent = messages?.[0]?.content;
        const threadTitle = firstMessageContent
          ? getMessageContentString(firstMessageContent)
          : targetThread.threadTitle;

        const newRepository =
          targetRepository?.repo ||
          targetRepository?.name ||
          targetThread.repository ||
          "Unknown Repository";

        const newBranch =
          targetRepository?.branch || targetThread.branch || "main";

        return currentThreads.map((thread) => {
          if (thread.thread_id === threadId) {
            return {
              ...thread,
              threadTitle,
              repository: newRepository,
              branch: newBranch,
              completedTasksCount,
              totalTasksCount,
              tasks: plan,
              proposedPlan,
            };
          }
          return thread;
        });
      });
    },
    [],
  );

  const getThread = useCallback(
    async (threadId: string): Promise<ThreadWithTasks | null> => {
      if (!apiUrl || !assistantId) return null;
      const client = createClient(apiUrl, getApiKey() ?? undefined);

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

    const { totalTasksCount, completedTasksCount } = getTaskCounts(plan);

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
    const client = createClient(apiUrl, getApiKey() ?? undefined);

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

  // Removed polling - now using real-time stream updates via updateThreadFromStream

  // Initial load
  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  const value = {
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
    refreshThreads,
    getThread,
    updateThreadFromStream,
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
