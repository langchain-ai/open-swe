import { ThreadWithTasks } from "@/providers/Thread";

// Future thread status filtering capability
export type ThreadStatus =
  | "idle"
  | "busy"
  | "error"
  | "interrupted"
  | "completed";

export interface ThreadFilter {
  statuses?: ThreadStatus[];
}

export interface PollConfig {
  interval: number;
  onUpdate: (
    updatedThreads: ThreadWithTasks[],
    changedThreadIds: string[],
  ) => void;
  onPollComplete: () => void;
  onError: (error: string) => void;
}

export class ThreadPoller {
  private config: PollConfig;
  private isPolling: boolean = false;
  private intervalId: NodeJS.Timeout | null = null;
  private getThreadsFn: () => ThreadWithTasks[];
  private getThreadFn: (threadId: string) => Promise<ThreadWithTasks | null>;

  constructor(
    config: PollConfig,
    getThreadsFn: () => ThreadWithTasks[],
    getThreadFn: (threadId: string) => Promise<ThreadWithTasks | null>,
  ) {
    this.config = config;
    this.getThreadsFn = getThreadsFn;
    this.getThreadFn = getThreadFn;
  }

  start(): void {
    if (this.isPolling) return;

    this.isPolling = true;
    this.intervalId = setInterval(() => {
      this.pollThreads();
    }, this.config.interval);
  }

  stop(): void {
    if (!this.isPolling) return;

    this.isPolling = false;
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
  }

  private async pollThreads(): Promise<void> {
    try {
      const currentThreads = this.getThreadsFn();

      // Poll first 10 threads only (matches sidebar pagination)
      const threadsToPool = currentThreads.slice(0, 10);
      const updatedThreads: ThreadWithTasks[] = [];
      const changedThreadIds: string[] = [];
      const errors: string[] = [];

      // Poll each thread individually
      for (const currentThread of threadsToPool) {
        try {
          const updatedThread = await this.getThreadFn(currentThread.thread_id);
          if (updatedThread) {
            updatedThreads.push(updatedThread);

            // Check if thread has changed
            if (this.hasThreadChanged(currentThread, updatedThread)) {
              changedThreadIds.push(updatedThread.thread_id);
            }
          }
        } catch (error) {
          errors.push(`Thread ${currentThread.thread_id}: ${error}`);
          // Continue polling other threads
          updatedThreads.push(currentThread); // Keep existing data
        }
      }

      // Report errors if any
      if (errors.length > 0) {
        this.config.onError(`Polling errors: ${errors.join(", ")}`);
      }

      // Update threads if we have any changes
      if (changedThreadIds.length > 0) {
        this.config.onUpdate(updatedThreads, changedThreadIds);
      }

      this.config.onPollComplete();
    } catch (error) {
      this.config.onError(`Polling failed: ${error}`);
    }
  }

  private hasThreadChanged(
    current: ThreadWithTasks,
    updated: ThreadWithTasks,
  ): boolean {
    // Compare key fields that might change
    return (
      current.completedTasksCount !== updated.completedTasksCount ||
      current.totalTasksCount !== updated.totalTasksCount ||
      current.status !== updated.status ||
      current.threadTitle !== updated.threadTitle ||
      current.repository !== updated.repository ||
      current.branch !== updated.branch ||
      JSON.stringify(current.tasks) !== JSON.stringify(updated.tasks) ||
      JSON.stringify(current.proposedPlan) !==
        JSON.stringify(updated.proposedPlan)
    );
  }
}
