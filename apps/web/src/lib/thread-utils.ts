import { ThreadSummary, TaskWithContext, PlanItem } from "@/types/index";
import { ThreadStatus } from "@langchain/langgraph-sdk";

/**
 * Determines which task is currently being executed based on plan progress
 */
export function getCurrentTaskIndex(plan: PlanItem[]): number {
  for (let i = 0; i < plan.length; i += 1) {
    if (!plan[i].completed) {
      return i;
    }
  }
  return -1;
}

/**
 * Gets the current task object (first uncompleted task)
 */
export function getCurrentTask<T extends PlanItem>(plan: T[]): T | null {
  return (
    plan.filter((p) => !p.completed).sort((a, b) => a.index - b.index)[0] ||
    null
  );
}

/**
 * Infers task status based on thread state and execution context
 * Now primarily uses the thread status from LangGraph SDK
 */
export function inferTaskStatus(
  task: PlanItem,
  taskIndex: number,
  threadValues: any,
  threadId: string,
  threadStatus: ThreadStatus = "idle",
): ThreadStatus {
  // If task is completed, but thread is still active, show thread status
  if (task.completed) {
    // For completed tasks, show idle unless thread has error
    return threadStatus === "error" ? "error" : "idle";
  }

  const plan = threadValues?.plan || [];
  const currentTaskIndex = getCurrentTaskIndex(plan);
  const isCurrentTask = taskIndex === currentTaskIndex;

  // For current task, use thread status directly
  if (isCurrentTask) {
    return threadStatus;
  }

  // Past tasks that aren't completed are in error state
  if (taskIndex < currentTaskIndex && !task.completed) {
    return "error";
  }

  // Future tasks default to interrupted if thread is not idle
  return threadStatus === "idle" ? "idle" : "interrupted";
}

/**
 * Groups an array of tasks into ThreadSummary objects
 * Each thread contains aggregated information about its tasks
 */
export function groupTasksIntoThreads(
  allTasks: TaskWithContext[],
): ThreadSummary[] {
  const threadSummaries: ThreadSummary[] = allTasks.reduce((acc, task) => {
    const existingThread = acc.find((t) => t.threadId === task.threadId);

    if (existingThread) {
      existingThread.tasks.push(task);
      existingThread.totalTasksCount += 1;
      if (task.status === "idle" && task.completed) {
        existingThread.completedTasksCount += 1;
      }
    } else {
      acc.push({
        threadId: task.threadId,
        threadTitle:
          task.threadTitle || `Thread ${task.threadId.substring(0, 8)}`,
        repository: task.repository || "Unknown Repository",
        branch: task.branch || "main",
        date:
          task.date ||
          new Date(task.createdAt).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
          }),
        createdAt: task.createdAt,
        tasks: [task],
        completedTasksCount: task.status === "idle" && task.completed ? 1 : 0,
        totalTasksCount: 1,
        status: task.status, // Will be overridden below
      });
    }

    return acc;
  }, [] as ThreadSummary[]);

  // Use the status from the first task (they should all have the same thread status)
  threadSummaries.forEach((thread) => {
    if (thread.tasks.length > 0) {
      thread.status = thread.tasks[0].status;
    }
  });

  return threadSummaries;
}

/**
 * Sorts threads by creation date (newest first)
 */
export function sortThreadsByDate(threads: ThreadSummary[]): ThreadSummary[] {
  return threads.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  );
}
