import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "URLParser");

interface URLParseResult {
  success: true;
  url: URL;
}

interface URLParseError {
  success: false;
  errorMessage: string;
}

type URLParseResponse = URLParseResult | URLParseError;

/**
 * Safely parses a URL string and returns a structured result.
 */
export function parseUrl(urlString: string): URLParseResponse {
  try {
    const parsedUrl = new URL(urlString);
    return {
      success: true,
      url: parsedUrl,
    };
  } catch (e) {
    const errorString = e instanceof Error ? e.message : String(e);
    logger.error("Failed to parse URL", { url: urlString, error: errorString });

    return {
      success: false,
      errorMessage: `Failed to parse URL: ${urlString}\nError:\n${errorString}.`,
    };
  }
}
