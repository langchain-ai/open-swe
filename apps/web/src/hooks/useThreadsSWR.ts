import useSWR from "swr";
import { Thread } from "@langchain/langgraph-sdk";
import { createClient } from "@/providers/client";
import { THREAD_SWR_CONFIG } from "@/lib/swr-config";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { ReviewerGraphState } from "@open-swe/shared/open-swe/reviewer/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { useMemo, useState } from "react";
import type { Installation } from "./useGitHubInstallations";

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
  currentInstallation?: Installation | null;
  disableOrgFiltering?: boolean;
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
    currentInstallation,
    disableOrgFiltering,
    pagination,
  } = options;
  const [hasMoreState, setHasMoreState] = useState(true);

  const paginationWithDefaults = {
    limit: 25,
    offset: 0,
    sortBy: "updated_at" as ThreadSortBy,
    sortOrder: "desc" as SortOrder,
    ...pagination,
  };

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
  }, [assistantId, paginationWithDefaults]);

  const fetcher = async (): Promise<Thread<TGraphState>[]> => {
    if (!apiUrl) {
      throw new Error("API URL is not configured");
    }

    const client = createClient(apiUrl);
    const searchArgs = assistantId
      ? {
          metadata: {
            graph_id: assistantId,
          },
          ...(paginationWithDefaults ? paginationWithDefaults : {}),
        }
      : paginationWithDefaults
        ? paginationWithDefaults
        : undefined;

    return await client.threads.search<TGraphState>(searchArgs);
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

    if (disableOrgFiltering) {
      return allThreads;
    }

    if (!allThreads.length) {
      setHasMoreState(false);
    }

    if (!currentInstallation) {
      setHasMoreState(false);
      return [];
    }

    return allThreads.filter((thread) => {
      const threadInstallationName = thread.metadata?.installation_name;
      return (
        typeof threadInstallationName === "string" &&
        threadInstallationName === currentInstallation.accountName
      );
    });
  }, [data, currentInstallation, disableOrgFiltering]);

  const hasMore = useMemo(() => {
    return hasMoreState && !!threads.length;
  }, [threads, paginationWithDefaults]);

  return {
    threads,
    error,
    isLoading,
    isValidating,
    mutate,
    hasMore,
  };
}
