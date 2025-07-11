/**
 * Standardized SWR configuration for thread-related hooks
 *
 * This ensures consistent polling intervals, error handling, and caching behavior
 * across all thread data fetching in the application.
 */
export const THREAD_SWR_CONFIG = {
  refreshInterval: 15000, // 15 seconds - standard polling interval
  revalidateOnFocus: false, // Avoid excessive API calls on focus
  revalidateOnReconnect: true, // Refresh when network reconnects
  errorRetryCount: 3, // Retry failed requests 3 times
  errorRetryInterval: 5000, // Wait 5 seconds between retries
  dedupingInterval: 2000, // Dedupe identical requests within 2 seconds
} as const;

/**
 * SWR configuration for thread status polling
 * Uses same intervals but with focus revalidation for real-time updates
 */
export const THREAD_STATUS_SWR_CONFIG = {
  ...THREAD_SWR_CONFIG,
  revalidateOnFocus: true, // Status updates benefit from focus revalidation
} as const;

/**
 * SWR configuration for one-time fetches (no polling)
 * Used for thread data that doesn't need real-time updates
 */
export const THREAD_STATIC_SWR_CONFIG = {
  ...THREAD_SWR_CONFIG,
  refreshInterval: 0, // No automatic polling
} as const;
