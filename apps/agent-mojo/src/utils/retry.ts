interface RetryOptions {
  retries?: number;
  delay?: number;
}

/**
 * Executes an async function with retry logic
 * @param fn - The async function to execute
 * @param options - Configuration options for retry behavior
 * @returns Promise that resolves with the function result or rejects with the last error
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {},
): Promise<T | Error | undefined> {
  const { retries = 3, delay = 0 } = options;

  let lastError: Error | undefined;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));

      if (attempt === retries) {
        return lastError;
      }

      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  return lastError;
}

/**
 * Creates a retry wrapper for a specific function with predefined options
 * @param fn - The async function to wrap
 * @param options - Configuration options for retry behavior
 * @returns A new function that will retry on failure
 */
export function createRetryWrapper<T extends any[], R>(
  fn: (...args: T) => Promise<R>,
  options: RetryOptions = {},
): (...args: T) => Promise<R | Error | undefined> {
  return (...args: T) => withRetry(() => fn(...args), options);
}
