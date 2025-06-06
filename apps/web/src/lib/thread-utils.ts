import { ThreadSummary, TaskWithContext } from "@/types/index";

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
