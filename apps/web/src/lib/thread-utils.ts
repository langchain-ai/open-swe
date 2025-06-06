import { ThreadSummary, TaskWithContext } from "@/types/index";

/**
 * Function to create simple, predictable task ID
 */
export function createTaskId(threadId: string, taskIndex: number): string {
  return `${threadId}-${taskIndex}`;
}

/**
 * Groups tasks into thread summaries
 */
export function groupTasksIntoThreads(
  tasks: TaskWithContext[],
): ThreadSummary[] {
  const threadMap = new Map<string, ThreadSummary>();

  tasks.forEach((task) => {
    if (!threadMap.has(task.threadId)) {
      threadMap.set(task.threadId, {
        threadId: task.threadId,
        threadTitle:
          task.threadTitle || `Thread ${task.threadId.substring(0, 8)}`,
        repository: task.repository || "Unknown Repository",
        branch: task.branch || "main",
        date:
          task.date ||
          new Date().toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
          }),
        createdAt: task.createdAt,
        tasks: [],
        completedTasksCount: 0,
        totalTasksCount: 0,
        status: task.status,
      });
    }

    const threadSummary = threadMap.get(task.threadId)!;
    threadSummary.tasks.push(task);
    threadSummary.totalTasksCount += 1;

    if (task.completed) {
      threadSummary.completedTasksCount += 1;
    }
  });

  return Array.from(threadMap.values());
}

/**
 * Sorts threads by creation date (newest first)
 */
export function sortThreadsByDate(threads: ThreadSummary[]): ThreadSummary[] {
  return threads.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  );
}
