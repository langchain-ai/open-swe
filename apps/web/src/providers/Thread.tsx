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

  if (!tasks || !tasks.tasks || tasks.tasks.length === 0) {
    console.log("📊 getTaskCounts: No tasks found, returning default counts");
    return defaultCounts;
  }

  // Access the nested structure: tasks.tasks[activeTaskIndex].planRevisions[activeRevisionIndex].plans
  const activeTaskIndex = tasks.activeTaskIndex;
  const activeTask = tasks.tasks[activeTaskIndex];

  console.log(
    "📊 getTaskCounts: activeTaskIndex =",
    activeTaskIndex,
    "activeTask =",
    activeTask,
  );

  if (
    !activeTask ||
    !activeTask.planRevisions ||
    activeTask.planRevisions.length === 0
  ) {
    console.log("📊 getTaskCounts: No active task or plan revisions found");
    return defaultCounts;
  }

  const activeRevisionIndex = activeTask.activeRevisionIndex;
  const activeRevision = activeTask.planRevisions[activeRevisionIndex];

  console.log(
    "📊 getTaskCounts: activeRevisionIndex =",
    activeRevisionIndex,
    "activeRevision =",
    activeRevision,
  );

  if (
    !activeRevision ||
    !activeRevision.plans ||
    activeRevision.plans.length === 0
  ) {
    console.log("📊 getTaskCounts: No active revision or plans found");
    return defaultCounts;
  }

  const plans = activeRevision.plans;
  console.log(
    "📊 getTaskCounts: plans =",
    plans.map((p) => ({ index: p.index, completed: p.completed })),
  );

  // Find the index of the first plan that is not completed
  // The number of completed tasks equals the index of the first incomplete task
  const firstIncompleteIndex = plans.findIndex((p) => !p.completed);

  // If all tasks are completed, use the total length
  // If some tasks are incomplete, use the index of the first incomplete task
  const completedTasksCount =
    firstIncompleteIndex === -1 ? plans.length : firstIncompleteIndex;

  console.log(
    "📊 getTaskCounts: firstIncompleteIndex =",
    firstIncompleteIndex,
    "completedTasksCount =",
    completedTasksCount,
    "totalTasksCount =",
    plans.length,
  );

  return {
    totalTasksCount: plans.length,
    completedTasksCount,
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
        console.log(
          `🌐 Polling API request for thread: ${threadId.substring(0, 8)}`,
        );

        const thread = await client.threads.get(threadId);

        // Get the comprehensive state data which contains the plan
        let stateData = null;
        if (
          "getState" in client.threads &&
          typeof client.threads.getState === "function"
        ) {
          try {
            console.log(`🎯 Getting state for ${threadId.substring(0, 8)}`);
            stateData = await (client.threads as any).getState(threadId);
            console.log(`📊 Thread state via getState:`, stateData);
          } catch (stateError) {
            console.log(`❌ getState failed:`, stateError);
          }
        }

        // Use state data for plan information, fallback to thread values
        const stateValues = stateData?.values;
        const threadValues = thread.values as any;
        const plan = stateValues?.plan || threadValues?.plan;

        console.log(`📋 Plan data source for ${threadId.substring(0, 8)}:`, {
          hasStateData: !!stateData,
          hasStateValues: !!stateValues,
          hasStatePlan: !!stateValues?.plan,
          hasThreadPlan: !!threadValues?.plan,
          usingStatePlan: !!stateValues?.plan,
          structure: stateValues?.plan
            ? "state.values.plan"
            : "thread.values.plan",
        });
        if (plan) {
          console.log(
            `📋 Raw plan structure for ${threadId.substring(0, 8)}:`,
            {
              activeTaskIndex: plan.activeTaskIndex,
              tasksLength: plan.tasks?.length,
              planStructure: {
                hasTasks: !!plan.tasks,
                tasksCount: plan.tasks?.length || 0,
              },
            },
          );

          // Follow exact nesting: values.plan.tasks[activeTaskIndex].planRevisions[activeRevisionIndex].plans
          if (
            plan.tasks &&
            plan.tasks.length > 0 &&
            typeof plan.activeTaskIndex === "number"
          ) {
            const activeTaskIndex = plan.activeTaskIndex;
            const activeTask = plan.tasks[activeTaskIndex];

            console.log(`📋 Active task for ${threadId.substring(0, 8)}:`, {
              activeTaskIndex,
              activeTask: activeTask
                ? {
                    id: activeTask.id,
                    taskIndex: activeTask.taskIndex,
                    activeRevisionIndex: activeTask.activeRevisionIndex,
                    planRevisionsLength: activeTask.planRevisions?.length,
                  }
                : null,
            });

            if (
              activeTask &&
              activeTask.planRevisions &&
              typeof activeTask.activeRevisionIndex === "number"
            ) {
              const activeRevisionIndex = activeTask.activeRevisionIndex;
              const activeRevision =
                activeTask.planRevisions[activeRevisionIndex];

              console.log(
                `📋 Active revision for ${threadId.substring(0, 8)}:`,
                {
                  activeRevisionIndex,
                  hasRevision: !!activeRevision,
                  plansLength: activeRevision?.plans?.length || 0,
                },
              );

              if (
                activeRevision &&
                activeRevision.plans &&
                Array.isArray(activeRevision.plans)
              ) {
                const plans = activeRevision.plans;
                const firstIncompleteIndex = plans.findIndex(
                  (p: any) => !p.completed,
                );
                const completedTasksCount =
                  firstIncompleteIndex === -1
                    ? plans.length
                    : firstIncompleteIndex;

                console.log(
                  `✅ Task completion analysis for ${threadId.substring(0, 8)}:`,
                  {
                    pathAccessed: `values.plan.tasks[${activeTaskIndex}].planRevisions[${activeRevisionIndex}].plans`,
                    totalPlans: plans.length,
                    firstIncompleteIndex,
                    completedTasksCount,
                    planStatuses: plans.map((p: any) => ({
                      index: p.index,
                      completed: p.completed,
                    })),
                    detailedPlans: plans.map((p: any) => ({
                      index: p.index,
                      completed: p.completed,
                      planPreview: p.plan
                        ? p.plan.substring(0, 50) + "..."
                        : "No plan text",
                    })),
                  },
                );
              } else {
                console.log(
                  `❌ No plans found in revision for ${threadId.substring(0, 8)}`,
                );
              }
            } else {
              console.log(
                `❌ No active revision found for ${threadId.substring(0, 8)}`,
              );
            }
          } else {
            console.log(
              `❌ No valid tasks found for ${threadId.substring(0, 8)}`,
            );
          }
        } else {
          console.log(
            `❌ No plan found in thread values for ${threadId.substring(0, 8)}`,
          );
        }

        return enhanceThreadWithTasks(thread, stateData);
      } catch (error) {
        console.error("Failed to fetch thread:", threadId, error);
        return null;
      }
    },
    [apiUrl, assistantId],
  );

  const enhanceThreadWithTasks = (
    thread: Thread,
    stateData?: any,
  ): ThreadWithTasks => {
    const stateValues = stateData?.values;
    const threadValues = thread.values as any;

    // Prefer state data over thread data for plan information
    const plan: TaskPlan | undefined = stateValues?.plan || threadValues?.plan;
    const proposedPlan: string[] =
      stateValues?.proposedPlan || threadValues?.proposedPlan || [];

    const targetRepository =
      stateValues?.targetRepository || threadValues?.targetRepository;
    const messages = stateValues?.messages || threadValues?.messages;
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

          // Get state data for comprehensive plan information
          let stateData = null;
          if (
            "getState" in client.threads &&
            typeof client.threads.getState === "function"
          ) {
            try {
              stateData = await (client.threads as any).getState(
                thread.thread_id,
              );
            } catch (stateError) {
              console.log(
                `Failed to get state for thread ${thread.thread_id}:`,
                stateError,
              );
            }
          }

          enhancedThreads.push(enhanceThreadWithTasks(fullThread, stateData));
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
