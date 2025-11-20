import useSWR from "swr";
import { Thread } from "@langchain/langgraph-sdk";
import { createClient } from "@/providers/client";
import { THREAD_SWR_CONFIG } from "@/lib/swr-config";
import {
  getAlternateThreadSearchMetadata,
  getThreadSearchMetadata,
  ThreadSearchMetadata,
} from "@/lib/thread";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import { ReviewerGraphState } from "@openswe/shared/open-swe/reviewer/types";
import { GraphState } from "@openswe/shared/open-swe/types";
import { useMemo, useState } from "react";

type ThreadSortBy = "thread_id" | "status" | "created_at" | "updated_at";
type SortOrder = "asc" | "desc";
/**
 * Union type representing all possible graph states in the Open SWE system
 */
export type AnyGraphState =
  | ManagerGraphState
  | PlannerGraphState
  | ReviewerGraphState
  | GraphState;

interface UseThreadsSWROptions {
  assistantId?: string;
  refreshInterval?: number;
  revalidateOnFocus?: boolean;
  revalidateOnReconnect?: boolean;
  /**
   * Pagination options
   */
  pagination?: {
    /**
     * Maximum number of threads to return.
     * @default 25
     */
    limit?: number;
    /**
     * Offset to start from.
     * @default 0
     */
    offset?: number;
    /**
     * Sort by.
     * @default "updated_at"
     */
    sortBy?: ThreadSortBy;
    /**
     * Sort order.
     * Must be one of 'asc' or 'desc'.
     * @default "desc"
     */
    sortOrder?: SortOrder;
  };
}

/**
 * Hook for fetching threads for any graph type.
 * Works with all graph states (Manager, Planner, Programmer, Reviewer)
 * by passing the appropriate assistantId.
 *
 * For UI display of manager threads, use `threadsToMetadata(threads)` utility to convert
 * raw threads to ThreadMetadata objects.
 */
export function useThreadsSWR<
  TGraphState extends AnyGraphState = AnyGraphState,
>(options: UseThreadsSWROptions = {}) {
  const {
    assistantId,
    refreshInterval = THREAD_SWR_CONFIG.refreshInterval,
    revalidateOnFocus = THREAD_SWR_CONFIG.revalidateOnFocus,
    revalidateOnReconnect = THREAD_SWR_CONFIG.revalidateOnReconnect,
    pagination,
  } = options;
  const [hasMoreState, setHasMoreState] = useState(true);

  const paginationWithDefaults = useMemo(
    () => ({
      limit: 25,
      offset: 0,
      sortBy: "updated_at" as ThreadSortBy,
      sortOrder: "desc" as SortOrder,
      ...pagination,
    }),
    [pagination],
  );

  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";

  // Create a unique key for SWR caching based on assistantId and pagination parameters
  const swrKey = useMemo(() => {
    const baseKey = assistantId ? ["threads", assistantId] : ["threads", "all"];
    if (pagination) {
      return [
        ...baseKey,
        paginationWithDefaults.limit,
        paginationWithDefaults.offset,
        paginationWithDefaults.sortBy,
        paginationWithDefaults.sortOrder,
      ];
    }
    return baseKey;
  }, [assistantId, pagination, paginationWithDefaults]);

  const THREAD_SEARCH_TIMEOUT_MS = 15000;

  const fetcher = async (): Promise<Thread<TGraphState>[]> => {
    if (!apiUrl) {
      throw new Error("API URL is not configured");
    }

    const client = createClient(apiUrl);

    const runSearchWithTimeout = async (
      metadata?: ThreadSearchMetadata,
    ): Promise<Thread<TGraphState>[]> => {
      const searchArgs = {
        ...paginationWithDefaults,
        ...(metadata ? { metadata } : {}),
      };

      const start = Date.now();
      let timeoutId: ReturnType<typeof setTimeout> | undefined;

      try {
        const searchPromise = client.threads.search<TGraphState>(searchArgs);
        const timeoutPromise = new Promise<Thread<TGraphState>[]>(
          (_, reject) => {
            timeoutId = setTimeout(
              () => reject(new Error("Thread search timed out")),
              THREAD_SEARCH_TIMEOUT_MS,
            );
          },
        );
        return await Promise.race([searchPromise, timeoutPromise]);
      } catch (error) {
        const duration = Date.now() - start;
        if ((error as Error)?.message === "Thread search timed out") {
          console.error(`Thread search timed out after ${duration}ms`, {
            assistantId,
            searchArgs,
          });
        } else {
          console.error("Failed to search threads", error, {
            assistantId,
            searchArgs,
          });
        }
        throw error;
      } finally {
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
      }
    };

    if (!assistantId) {
      return runSearchWithTimeout();
    }

    let threads = await runSearchWithTimeout(
      getThreadSearchMetadata(assistantId),
    );

    if (threads.length === 0) {
      threads = await runSearchWithTimeout(
        getAlternateThreadSearchMetadata(assistantId),
      );
    }

    return threads;
  };

  const { data, error, isLoading, mutate, isValidating } = useSWR(
    swrKey,
    fetcher,
    {
      refreshInterval,
      revalidateOnFocus,
      revalidateOnReconnect,
      errorRetryCount: THREAD_SWR_CONFIG.errorRetryCount,
      errorRetryInterval: THREAD_SWR_CONFIG.errorRetryInterval,
      dedupingInterval: THREAD_SWR_CONFIG.dedupingInterval,
    },
  );

  const threads = useMemo(() => {
    const allThreads = data ?? [];
    if (!allThreads.length) {
      setHasMoreState(false);
    }
    return allThreads;
  }, [data]);

  const hasMore = useMemo(() => {
    return hasMoreState && !!threads.length;
  }, [threads, hasMoreState]);

  return {
    threads,
    error,
    isLoading,
    isValidating,
    mutate,
    hasMore,
  };
}
