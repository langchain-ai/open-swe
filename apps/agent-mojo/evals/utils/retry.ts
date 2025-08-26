import { createLogger, LogLevel } from "../../src/utils/logger.js";

const logger = createLogger(LogLevel.DEBUG, "Retry");

const RETRY_CONFIG = {
  maxRetries: 5,
  baseDelay: 1000,
  maxDelay: 30000,
  backoffMultiplier: 2,
  timeoutErrors: ["UND_ERR_HEADERS_TIMEOUT"],
};

/**
 * Retry decorator with exponential backoff for LangGraph client
 * operations.
 */
export async function withRetry<T>(operation: () => Promise<T>): Promise<T> {
  let lastError: any;

  for (let attempt = 0; attempt < RETRY_CONFIG.maxRetries; attempt++) {
    try {
      return await operation();
    } catch (error: any) {
      lastError = error;

      const isRetryable = RETRY_CONFIG.timeoutErrors.includes(
        error?.cause?.code,
      );

      if (isRetryable && attempt < RETRY_CONFIG.maxRetries - 1) {
        const delay = Math.min(
          RETRY_CONFIG.baseDelay *
            Math.pow(RETRY_CONFIG.backoffMultiplier, attempt),
          RETRY_CONFIG.maxDelay,
        );
        logger.info(
          `Retrying operation in ${delay}ms. Attempt ${attempt + 1} of ${RETRY_CONFIG.maxRetries}`,
          {
            attempt,
            lastError,
          },
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
      } else {
        throw lastError;
      }
    }
  }

  throw lastError;
}
