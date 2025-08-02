/**
 * Standardized SWR configuration for thread-related hooks
 *
 * This ensures consistent polling intervals, error handling, and caching behavior
 * across all thread data fetching in the application.
 */
export const THREAD_SWR_CONFIG = {
  refreshInterval: 15000,
  revalidateOnFocus: false,
  revalidateOnReconnect: true,
  errorRetryCount: 5,
  errorRetryInterval: 1000,
  dedupingInterval: 2000,
} as const;

/**
 * SWR configuration for initial thread loading
 * Aggressive for first load only
 */
export const THREAD_INITIAL_LOADING_SWR_CONFIG = {
  refreshInterval: 1000,
  revalidateOnFocus: true,
  revalidateOnReconnect: true,
  errorRetryCount: 10,
  errorRetryInterval: 100,
  dedupingInterval: 100,
} as const;

/**
 * SWR configuration for thread status polling
 * Uses same intervals but with focus revalidation for real-time updates
 */
export const THREAD_STATUS_SWR_CONFIG = {
  ...THREAD_SWR_CONFIG,
  revalidateOnFocus: true,
  refreshInterval: 3000,
  dedupingInterval: 1000,
} as const;

/**
 * SWR configuration for high-frequency task plan polling
 * Used when actively viewing a thread for real-time progress updates
 */
export const TASK_PLAN_SWR_CONFIG = {
  refreshInterval: 2000,
  revalidateOnFocus: true,
  revalidateOnReconnect: true,
  errorRetryCount: 5,
  errorRetryInterval: 500,
  dedupingInterval: 500,
} as const;

/**
 * SWR configuration for one-time fetches (no polling)
 * Used for thread data that doesn't need real-time updates
 */
export const THREAD_STATIC_SWR_CONFIG = {
  ...THREAD_SWR_CONFIG,
  refreshInterval: 0, // No automatic polling
} as const;
