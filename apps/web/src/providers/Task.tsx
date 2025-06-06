import { getApiKey } from "@/lib/api-key";
import {
  createContext,
  useContext,
  ReactNode,
  useCallback,
  useState,
  useRef,
  useEffect,
} from "react";
import { createClient } from "./client";
import {
  TaskContextType,
  TaskWithContext,
  TaskWithStatus,
} from "@/types/index";
import { ThreadStatus } from "@langchain/langgraph-sdk";

// Function to create simple, predictable task ID
function createTaskId(threadId: string, taskIndex: number): string {
  return `${threadId}-${taskIndex}`;
}

// Tasks use the same status type as threads, so we can use thread status directly

const TaskContext = createContext<TaskContextType | undefined>(undefined);

export function TaskProvider({ children }: { children: ReactNode }) {
  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";
  const assistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID ?? "";

  const [tasks, setTasks] = useState<TaskWithStatus[]>([]);
  const [allTasks, setAllTasks] = useState<TaskWithContext[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);

  // Track active threads for real-time status inference
  const [activeThreads, setActiveThreads] = useState<Set<string>>(new Set());

  // Add debounce to prevent multiple rapid calls
  const lastCallTime = useRef<number>(0);
  const DEBOUNCE_MS = 1000; // Wait 1 second between calls

  // Add polling for real-time updates
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Function to add thread to active tracking
  const addActiveThread = useCallback((threadId: string) => {
    setActiveThreads((prev) => new Set(prev).add(threadId));
  }, []);

  // Function to remove thread from active tracking
  const removeActiveThread = useCallback((threadId: string) => {
    setActiveThreads((prev) => {
      const newSet = new Set(prev);
      newSet.delete(threadId);
      return newSet;
    });
  }, []);

  const getTasks = useCallback(
    async (threadId: string): Promise<TaskWithStatus[]> => {
      if (!apiUrl || !assistantId || !threadId) return [];
      const client = createClient(apiUrl, getApiKey() ?? undefined);

      try {
        const thread = await client.threads.get(threadId);
        const threadValues = thread.values as any;
        const plan = threadValues?.plan || [];

        // Use thread status directly from LangGraph SDK
        const threadStatus = (thread.status ?? "idle") as ThreadStatus;

        return plan.map((planItem: any, index: number) => ({
          ...planItem,
          status: threadStatus,
          repository:
            threadValues?.targetRepository?.repo ||
            threadValues?.targetRepository?.name,
          date: new Date().toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
          }),
        }));
      } catch (error) {
        console.error("Failed to fetch tasks for thread:", threadId, error);
        return [];
      }
    },
    [apiUrl, assistantId],
  );

  const getAllTasks = useCallback(async (): Promise<TaskWithContext[]> => {
    const callId = Math.random().toString(36).substring(7);

    const now = Date.now();
    if (now - lastCallTime.current < DEBOUNCE_MS) {
      return [];
    }
    lastCallTime.current = now;

    if (!apiUrl || !assistantId) {
      console.error(`❌ [${callId}] Missing API URL or Assistant ID`);
      return [];
    }

    const client = createClient(apiUrl, getApiKey() ?? undefined);

    try {
      // Search for all threads
      const searchParams = {
        limit: 200, // Increased from 50 to handle more threads
        metadata: assistantId.includes("-")
          ? { graph_id: assistantId }
          : { assistant_id: assistantId },
      };

      let threadsResponse: any[] = [];
      let alternativeResponse: any[] = [];
      let noMetadataResponse: any[] = [];

      try {
        threadsResponse = await client.threads.search(searchParams);
      } catch (error) {
        console.error(`❌ [${callId}] Primary search failed:`, error);
        threadsResponse = [];
      }

      // Let's also try both search criteria to see if there's a mismatch
      const alternativeSearchParams = {
        limit: 200,
        metadata: assistantId.includes("-")
          ? { assistant_id: assistantId } // Try the opposite
          : { graph_id: assistantId },
      };

      try {
        alternativeResponse = await client.threads.search(
          alternativeSearchParams,
        );
      } catch (error) {
        console.error(`❌ [${callId}] Alternative search failed:`, error);
        alternativeResponse = [];
      }

      // Let's also try searching without metadata to see if there are ANY threads

      try {
        noMetadataResponse = await client.threads.search({ limit: 10 });

        if (noMetadataResponse.length > 0) {
          console.log(
            `📝 [${callId}] Sample threads found:`,
            noMetadataResponse.slice(0, 3).map((t) => ({
              id: t.thread_id,
              metadata: t.metadata,
              created_at: t.created_at,
            })),
          );
        }
      } catch (error) {
        console.error(`❌ [${callId}] No-metadata search failed:`, error);
        noMetadataResponse = [];
      }

      // Use whichever search returned more results
      const bestResponse =
        threadsResponse.length >= alternativeResponse.length
          ? threadsResponse
          : alternativeResponse;

      const allTasksWithContext: TaskWithContext[] = [];
      const failedThreads: string[] = [];
      const threadsWithNoTasks: string[] = [];

      // Process each thread to extract tasks
      for (const threadSummary of bestResponse) {
        try {
          const thread = await client.threads.get(threadSummary.thread_id);
          const threadValues = thread.values as any;

          // Use thread status directly from LangGraph SDK
          const threadStatus = (thread.status ?? "idle") as ThreadStatus;

          const plan: any[] = threadValues?.plan || [];
          const proposedPlan: any[] = threadValues?.proposedPlan || [];
          const rawTasks = plan.length > 0 ? plan : proposedPlan;

          if (rawTasks.length === 0) {
            threadsWithNoTasks.push(threadSummary.thread_id);
            continue;
          }

          const targetRepository = threadValues?.targetRepository;
          const messages = (threadValues as any)?.messages;
          const threadTitle =
            messages?.[0]?.content?.[0]?.text?.substring(0, 50) + "..." ||
            `Thread ${threadSummary.thread_id.substring(0, 8)}`;

          rawTasks.forEach((rawTask) => {
            const taskData =
              typeof rawTask === "string"
                ? {
                    index: rawTasks.indexOf(rawTask),
                    plan: rawTask,
                    completed: false,
                    summary: undefined,
                  }
                : rawTask;

            const taskIndex = rawTasks.indexOf(rawTask);

            const processedTask = {
              ...taskData,
              status: threadStatus,
              taskId: createTaskId(threadSummary.thread_id, taskIndex),
              threadId: threadSummary.thread_id,
              threadTitle,
              repository:
                targetRepository?.repo ||
                targetRepository?.name ||
                "Unknown Repository",
              branch: targetRepository?.branch || "main",
              date: new Date(threadSummary.created_at).toLocaleDateString(
                "en-US",
                {
                  month: "short",
                  day: "numeric",
                },
              ),
              createdAt: threadSummary.created_at,
            };

            allTasksWithContext.push(processedTask);
          });
        } catch (error) {
          console.error(
            `Failed to fetch thread ${threadSummary.thread_id}:`,
            error,
          );
          failedThreads.push(threadSummary.thread_id);
        }
      }

      // Sort by repository, then by creation date (newest first)
      return allTasksWithContext.sort((a, b) => {
        const repoCompare = a.repository!.localeCompare(b.repository!);
        if (repoCompare !== 0) return repoCompare;
        return (
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
        );
      });
    } catch (error) {
      console.error("Failed to fetch all tasks:", error);
      return [];
    }
  }, [apiUrl, assistantId]);

  // Setup polling for real-time updates
  // TODO: improve real time status updates
  useEffect(() => {
    if (activeThreads.size > 0) {
      // Immediately poll when threads become active
      getAllTasks().then(setAllTasks).catch(console.error);

      // Set up regular polling every 2 seconds (faster during execution)
      pollIntervalRef.current = setInterval(async () => {
        try {
          const newTasks = await getAllTasks();

          setAllTasks((prevTasks) => {
            if (prevTasks.length !== newTasks.length) {
              return newTasks;
            }

            // Deep comparison of task statuses and IDs to see if anything meaningful changed
            const hasChanges = prevTasks.some((prevTask, index) => {
              const newTask = newTasks[index];
              return (
                !newTask ||
                prevTask.taskId !== newTask.taskId ||
                prevTask.status !== newTask.status ||
                prevTask.completed !== newTask.completed
              );
            });

            return hasChanges ? newTasks : prevTasks;
          });
        } catch (error) {
          console.error("Polling error:", error);
        }
      }, 2000); // Faster polling during execution
    } else {
      // Stop polling when no active threads
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
  }, [activeThreads.size, getAllTasks]);

  // Add a function to manually trigger status refresh
  const refreshStatus = useCallback(async () => {
    try {
      const newTasks = await getAllTasks();
      setAllTasks(newTasks);
    } catch (error) {
      console.error("Failed to refresh status:", error);
    }
  }, [getAllTasks]);

  useEffect(() => {
    if (!apiUrl || !assistantId) return;

    setTasksLoading(true);

    getAllTasks()
      .then((tasks) => {
        setAllTasks(tasks);

        const potentiallyActiveThreads = new Set<string>();
        tasks.forEach((task) => {
          // Consider any non-idle thread as potentially active
          if (task.status !== "idle") {
            potentiallyActiveThreads.add(task.threadId);
          }
        });

        if (potentiallyActiveThreads.size > 0) {
          setActiveThreads(potentiallyActiveThreads);
        }
      })
      .catch(console.error)
      .finally(() => setTasksLoading(false));
  }, [apiUrl, assistantId, getAllTasks]);

  const value = {
    getTasks,
    getAllTasks,
    refreshStatus,
    tasks,
    setTasks,
    allTasks,
    setAllTasks,
    tasksLoading,
    setTasksLoading,
    addActiveThread,
    removeActiveThread,
    activeThreads,
  };

  return <TaskContext.Provider value={value}>{children}</TaskContext.Provider>;
}

export function useTasks() {
  const context = useContext(TaskContext);
  if (context === undefined) {
    throw new Error("useTasks must be used within a TaskProvider");
  }
  return context;
}
