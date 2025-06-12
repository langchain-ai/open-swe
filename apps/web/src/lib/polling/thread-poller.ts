import { Thread } from "@langchain/langgraph-sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getThreadTasks, getThreadTitle } from "../thread";

export interface PollConfig {
  interval: number;
  onUpdate: (
    updatedThreads: Thread<GraphState>[],
    changedThreadIds: string[],
  ) => void;
}

export class ThreadPoller {
  private config: PollConfig;
  private isPolling: boolean = false;
  private intervalId: NodeJS.Timeout | null = null;
  private threads: Thread<GraphState>[];
  private getThreadFn: (threadId: string) => Promise<Thread<GraphState> | null>;

  constructor(
    config: PollConfig,
    threads: Thread<GraphState>[],
    getThreadFn: (threadId: string) => Promise<Thread<GraphState> | null>,
  ) {
    this.config = config;
    this.threads = threads;
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
      const currentThreads = this.threads;

      const threadsToPool = currentThreads.slice(0, 10);
      const updatedThreads: Thread<GraphState>[] = [];
      const changedThreadIds: string[] = [];
      const errors: string[] = [];

      const pollUpdatePromise = Promise.allSettled(
        threadsToPool.map(async (currentThread) => {
          try {
            const updatedThread = await this.getThreadFn(
              currentThread.thread_id,
            );
            if (updatedThread) {
              updatedThreads.push(updatedThread);

              if (this.hasThreadChanged(currentThread, updatedThread)) {
                changedThreadIds.push(updatedThread.thread_id);
              }
            }
          } catch (error) {
            errors.push(`Thread ${currentThread.thread_id}: ${error}`);
            updatedThreads.push(currentThread);
          }
        }),
      );

      await pollUpdatePromise;

      if (changedThreadIds.length > 0) {
        this.config.onUpdate(updatedThreads, changedThreadIds);
      }
    } catch (error) {
      console.error("Thread polling error:", error);
    }
  }

  private hasThreadChanged(
    current: Thread<GraphState>,
    updated: Thread<GraphState>,
  ): boolean {
    const currentTaskCounts = getThreadTasks(current);
    const updatedTaskCounts = getThreadTasks(updated);
    const currentTargetRepo = current.values?.targetRepository;
    const updatedTargetRepo = updated.values?.targetRepository;
    return (
      currentTaskCounts.completedTasks !== updatedTaskCounts.completedTasks ||
      currentTaskCounts.totalTasks !== updatedTaskCounts.totalTasks ||
      current.status !== updated.status ||
      getThreadTitle(current) !== getThreadTitle(updated) ||
      currentTargetRepo.repo !== updatedTargetRepo.repo ||
      currentTargetRepo.branch !== updatedTargetRepo.branch ||
      JSON.stringify(current.values?.plan) !==
        JSON.stringify(updated.values?.plan) ||
      JSON.stringify(current.values?.proposedPlan) !==
        JSON.stringify(updated.values?.proposedPlan)
    );
  }
}
