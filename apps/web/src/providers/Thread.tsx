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
  useRef,
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

  // Simple polling for active threads only
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

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

    // Use plan if it exists (contains completion status), otherwise use proposedPlan
    const rawTasks = plan.length > 0 ? plan : proposedPlan;

    const tasks = rawTasks.map((rawTask, index) => {
      if (typeof rawTask === "string") {
        // For string tasks (from proposedPlan), default to not completed
        return {
          index,
          plan: rawTask,
          completed: false,
          summary: undefined,
        };
      }
      // For object tasks (from plan), preserve existing completion status and other properties
      return {
        index: rawTask.index ?? index,
        plan: rawTask.plan,
        completed: rawTask.completed ?? false,
        summary: rawTask.summary,
      };
    });

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

  // Simple polling for busy threads
  useEffect(() => {
    const busyThreads = threads.filter((t) => t.status === "busy");

    if (busyThreads.length > 0) {
      pollIntervalRef.current = setInterval(() => {
        refreshThreads().catch(console.error);
      }, 3000); // Poll every 3 seconds
    } else {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    }

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [threads, refreshThreads]);

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
