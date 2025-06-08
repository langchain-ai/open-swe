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

// Enhanced thread with task completion info
export interface ThreadWithTasks extends Thread {
  threadTitle: string;
  repository: string;
  branch: string;
  completedTasksCount: number;
  totalTasksCount: number;
  tasks: Array<{
    index: number;
    plan: string;
    completed: boolean;
    summary?: string;
  }>;
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

        const plan: any[] = streamValues?.plan || [];
        const proposedPlan: any[] = streamValues?.proposedPlan || [];
        const targetRepository = streamValues?.targetRepository;
        const messages = streamValues?.messages;

        // Use plan if it exists (contains completion status), otherwise use proposedPlan
        const rawTasks = plan.length > 0 ? plan : proposedPlan;

        const tasks = rawTasks.map((rawTask: any, index: number) => {
          if (typeof rawTask === "string") {
            return {
              index,
              plan: rawTask,
              completed: false,
              summary: undefined,
            };
          }
          return {
            index: rawTask.index ?? index,
            plan: rawTask.plan,
            completed: rawTask.completed ?? false,
            summary: rawTask.summary,
          };
        });

        const completedTasksCount = tasks.filter(
          (task) => task.completed,
        ).length;

        // Extract thread title from messages if available
        const threadTitle =
          messages?.[0]?.content?.[0]?.text?.substring(0, 50) + "..." ||
          targetThread.threadTitle || // Keep existing title if no new one
          `Thread ${targetThread.thread_id.substring(0, 8)}`;

        const newRepository =
          targetRepository?.repo ||
          targetRepository?.name ||
          targetThread.repository ||
          "Unknown Repository";

        const newBranch =
          targetRepository?.branch || targetThread.branch || "main";

        // Check if any values actually changed to prevent unnecessary updates
        const hasChanges =
          targetThread.completedTasksCount !== completedTasksCount ||
          targetThread.totalTasksCount !== tasks.length ||
          targetThread.threadTitle !== threadTitle ||
          targetThread.repository !== newRepository ||
          targetThread.branch !== newBranch ||
          JSON.stringify(targetThread.tasks) !== JSON.stringify(tasks);

        if (!hasChanges) {
          return currentThreads; // No changes, return same array to prevent re-render
        }

        return currentThreads.map((thread) => {
          if (thread.thread_id === threadId) {
            return {
              ...thread,
              threadTitle,
              repository: newRepository,
              branch: newBranch,
              completedTasksCount,
              totalTasksCount: tasks.length,
              tasks,
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
    const plan: any[] = threadValues?.plan || [];
    const proposedPlan: any[] = threadValues?.proposedPlan || [];

    // Improved logic: If plan exists, use it; otherwise use proposedPlan
    // But also handle cases where plan exists but might be incomplete
    let tasks: Array<{
      index: number;
      plan: string;
      completed: boolean;
      summary?: string;
    }> = [];

    if (plan.length > 0) {
      // Plan exists - use it as it contains completion status
      tasks = plan.map((rawTask, index) => {
        if (typeof rawTask === "string") {
          return {
            index,
            plan: rawTask,
            completed: false,
            summary: undefined,
          };
        }
        return {
          index: rawTask.index ?? index,
          plan: rawTask.plan,
          completed: rawTask.completed ?? false,
          summary: rawTask.summary,
        };
      });
    } else if (proposedPlan.length > 0) {
      // Only proposedPlan exists - these are not started yet, so all incomplete
      tasks = proposedPlan.map((rawTask, index) => {
        if (typeof rawTask === "string") {
          return {
            index,
            plan: rawTask,
            completed: false,
            summary: undefined,
          };
        }
        return {
          index: rawTask.index ?? index,
          plan: rawTask.plan || rawTask,
          completed: false, // ProposedPlan items are never completed
          summary: undefined,
        };
      });
    }

    const completedTasksCount = tasks.filter((task) => task.completed).length;
    const targetRepository = threadValues?.targetRepository;
    const messages = (threadValues as any)?.messages;
    const threadTitle =
      messages?.[0]?.content?.[0]?.text?.substring(0, 50) + "..." ||
      `Thread ${thread.thread_id.substring(0, 8)}`;

    return {
      ...thread,
      threadTitle,
      repository:
        targetRepository?.repo ||
        targetRepository?.name ||
        "Unknown Repository",
      branch: targetRepository?.branch || "main",
      completedTasksCount,
      totalTasksCount: tasks.length,
      tasks,
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
