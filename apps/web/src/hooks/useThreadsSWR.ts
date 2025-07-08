import { createClient } from "@/providers/client";
import { Thread } from "@langchain/langgraph-sdk";
import useSWR from "swr";

export interface UseThreadsSWROptions {
  assistantId?: string;
  refreshInterval?: number;
  revalidateOnFocus?: boolean;
  revalidateOnReconnect?: boolean;
}

export function useThreadsSWR<State extends Record<string, any>>(
  options: UseThreadsSWROptions = {},
) {
  const {
    assistantId,
    refreshInterval = 0, // Default to no polling, can be overridden
    revalidateOnFocus = true,
    revalidateOnReconnect = true,
  } = options;

  const apiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL ?? "";

  // Create a unique key for SWR caching based on assistantId
  const swrKey = assistantId ? ["threads", assistantId] : ["threads", "all"];

  const fetcher = async (): Promise<Thread<State>[]> => {
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

    return await client.threads.search<State>(searchArgs);
  };

  const { data, error, isLoading, mutate, isValidating } = useSWR(
    swrKey,
    fetcher,
    {
      refreshInterval,
      revalidateOnFocus,
      revalidateOnReconnect,
      errorRetryCount: 3,
      errorRetryInterval: 5000,
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
