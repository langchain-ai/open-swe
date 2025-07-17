/**
 * Standardized SWR configuration for thread-related hooks
 *
 * This ensures consistent polling intervals, error handling, and caching behavior
 * across all thread data fetching in the application.
 */
export const THREAD_SWR_CONFIG = {
  refreshInterval: 3000, // Reduced from 15s to 3s for better real-time experience
  revalidateOnFocus: false,
  revalidateOnReconnect: true,
  errorRetryCount: 3,
  errorRetryInterval: 5000,
  dedupingInterval: 2000,
} as const;

/**
 * SWR configuration for thread status polling
 * Uses same intervals but with focus revalidation for real-time updates
 */
export const THREAD_STATUS_SWR_CONFIG = {
  ...THREAD_SWR_CONFIG,
  revalidateOnFocus: true,
  dedupingInterval: 2000, // Reduced for more responsive updates
} as const;

/**
 * SWR configuration for one-time fetches (no polling)
 * Used for thread data that doesn't need real-time updates
 */
export const THREAD_STATIC_SWR_CONFIG = {
  ...THREAD_SWR_CONFIG,
  refreshInterval: 0, // No automatic polling
} as const;
