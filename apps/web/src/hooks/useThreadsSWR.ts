import useSWR from "swr";
import { Thread } from "@langchain/langgraph-sdk";
import { createClient } from "@/providers/client";
import { THREAD_SWR_CONFIG } from "@/lib/swr-config";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { ReviewerGraphState } from "@open-swe/shared/open-swe/reviewer/types";
import { GraphState } from "@open-swe/shared/open-swe/types";

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
  } = options;

  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";

  // Create a unique key for SWR caching based on assistantId
  const swrKey = assistantId ? ["threads", assistantId] : ["threads", "all"];

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
        }
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

  return {
    threads: data ?? [],
    error,
    isLoading,
    isValidating,
    mutate, // For manual revalidation
  };
}
