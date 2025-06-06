import {
  ThreadSummary,
  TaskWithContext,
  TaskStatus,
  PlanItem,
} from "@/types/index";

/**
 * Analyzes thread state to determine if it has an error
 */
function hasThreadError(threadValues: any): boolean {
  const messages = threadValues?.messages || [];
  const recentMessages = messages.slice(-5);

  return recentMessages.some((msg: any) => {
    const content = msg.content;
    if (typeof content === "string") {
      return (
        content.toLowerCase().includes("error") ||
        content.toLowerCase().includes("failed") ||
        content.toLowerCase().includes("exception")
      );
    }
    if (Array.isArray(content)) {
      return content.some(
        (c: any) =>
          c.text &&
          (c.text.toLowerCase().includes("error") ||
            c.text.toLowerCase().includes("failed") ||
            c.text.toLowerCase().includes("exception")),
      );
    }
    return false;
  });
}

/**
 * Analyzes thread state to determine if it has a human interrupt
 */
function hasThreadInterrupt(threadValues: any): boolean {
  const messages = threadValues?.messages || [];
  const lastMessage = messages[messages.length - 1];

  if (lastMessage?.type === "ai" && lastMessage.tool_calls?.length > 0) {
    const pendingToolCalls = lastMessage.tool_calls.some((toolCall: any) => {
      const responseExists = messages.some(
        (msg: any) => msg.type === "tool" && msg.tool_call_id === toolCall.id,
      );
      return !responseExists;
    });
    return pendingToolCalls;
  }

  return false;
}

/**
 * Determines which task is currently being executed based on plan progress
 */
function getCurrentTaskIndex(plan: PlanItem[]): number {
  for (let i = 0; i < plan.length; i += 1) {
    if (!plan[i].completed) {
      return i;
    }
  }
  return -1;
}

/**
 * Checks if a thread is actively running by analyzing execution state
 */
function isThreadActive(
  threadId: string,
  allActiveThreads: Set<string> = new Set(),
): boolean {
  // For now, we'll determine this based on whether the thread has active stream context
  // This will be enhanced when we connect to the streaming infrastructure
  return allActiveThreads.has(threadId);
}

/**
 * Infers task status based on thread state and execution context
 */
export function inferTaskStatus(
  task: PlanItem,
  taskIndex: number,
  threadValues: any,
  threadId: string,
  allActiveThreads: Set<string> = new Set(),
): TaskStatus {
  // If task is completed, it's done
  if (task.completed) {
    return "done";
  }

  const plan = threadValues?.plan || [];
  const currentTaskIndex = getCurrentTaskIndex(plan);
  const isCurrentTask = taskIndex === currentTaskIndex;
  const hasError = hasThreadError(threadValues);
  const hasInterrupt = hasThreadInterrupt(threadValues);
  const isActive = isThreadActive(threadId, allActiveThreads);

  if (isCurrentTask) {
    if (hasError) {
      return "error";
    }

    // Check for interrupt state
    if (hasInterrupt) {
      return "interrupted";
    }

    // If thread is active and this is current task, it's running
    if (isActive) {
      return "running";
    }
  }

  // Past tasks that aren't completed are in error state
  if (taskIndex < currentTaskIndex && !task.completed) {
    return "error";
  }

  // Future tasks or paused current task default to interrupted
  return "interrupted";
}

/**
 * Enhanced version that can be called with active thread tracking
 */
export function inferTaskStatusWithContext(
  task: PlanItem,
  taskIndex: number,
  threadValues: any,
  threadId: string,
  activeThreads: Set<string>,
): TaskStatus {
  return inferTaskStatus(
    task,
    taskIndex,
    threadValues,
    threadId,
    activeThreads,
  );
}

/**
 * Determines the overall status of a thread based on its constituent tasks
 * Priority: error > running > done > interrupted
 */
export function determineThreadStatus(
  tasks: TaskWithContext[],
): "running" | "interrupted" | "done" | "error" {
  const hasRunning = tasks.some((t) => t.status === "running");
  const hasError = tasks.some((t) => t.status === "error");
  const allDone = tasks.every((t) => t.status === "done");

  if (hasError) {
    return "error";
  } else if (hasRunning) {
    return "running";
  } else if (allDone) {
    return "done";
  } else {
    return "interrupted";
  }
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
      if (task.status === "done") {
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
        completedTasksCount: task.status === "done" ? 1 : 0,
        totalTasksCount: 1,
        status: task.status, // Will be overridden below
      });
    }

    return acc;
  }, [] as ThreadSummary[]);

  // Determine overall thread status for each thread
  threadSummaries.forEach((thread) => {
    thread.status = determineThreadStatus(thread.tasks);
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
