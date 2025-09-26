/* eslint-disable no-console */
import { getConfig } from "@langchain/langgraph";

export enum LogLevel {
  DEBUG = "debug",
  INFO = "info",
  WARN = "warn",
  ERROR = "error",
}

// ANSI escape codes
const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";

// Define a list of colors (foreground)
const COLORS = [
  "\x1b[31m", // Red
  "\x1b[32m", // Green
  "\x1b[33m", // Yellow
  "\x1b[34m", // Blue
  "\x1b[35m", // Magenta
  "\x1b[36m", // Cyan
  "\x1b[91m", // Bright Red
  "\x1b[92m", // Bright Green
  "\x1b[93m", // Bright Yellow
  "\x1b[94m", // Bright Blue
  "\x1b[95m", // Bright Magenta
  "\x1b[96m", // Bright Cyan
];

// Simple hashing function to get a positive integer
function simpleHash(str: string): number {
  let hash = 0;
  if (str.length === 0) {
    return hash;
  }
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0; // Convert to 32bit integer
  }
  return Math.abs(hash); // Ensure positive for modulo index
}

const SENSITIVE_KEY_PATTERNS = [
  /token/i,
  /secret/i,
  /password/i,
  /authorization/i,
  /cookie/i,
  /api[_-]?key/i,
  /workspaces?root/i,
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function maskValue(value: unknown): unknown {
  if (value instanceof Date) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(() => "[redacted]");
  }
  if (isRecord(value)) {
    return Object.fromEntries(Object.keys(value).map((key) => [key, "[redacted]"]));
  }
  return "[redacted]";
}

function sanitizeLogData(data: unknown): unknown {
  if (data === undefined || data === null) {
    return data;
  }

  if (Array.isArray(data)) {
    return data.map((item) => sanitizeLogData(item));
  }

  if (!isRecord(data)) {
    return data;
  }

  return Object.fromEntries(
    Object.entries(data).map(([key, value]) => {
      const normalizedKey = key.toLowerCase();
      if (normalizedKey === "env" || normalizedKey === "environment") {
        return [key, maskValue(value)];
      }
      if (SENSITIVE_KEY_PATTERNS.some((pattern) => pattern.test(normalizedKey))) {
        return [key, maskValue(value)];
      }
      return [key, sanitizeLogData(value)];
    }),
  );
}

// Helper function to safely extract thread_id and run_id from LangGraph config
function getThreadAndRunIds(): { thread_id?: string; run_id?: string } {
  try {
    const config = getConfig();
    return {
      thread_id: config.configurable?.thread_id,
      run_id: config.configurable?.run_id,
    };
  } catch {
    // If getConfig throws an error or config.configurable is undefined,
    // return empty object and proceed as normal
    return {};
  }
}

function logWithOptionalIds(styledPrefix: string, message: string, data?: any) {
  const ids = getThreadAndRunIds();
  const sanitizedData = data !== undefined ? sanitizeLogData(data) : undefined;
  if (Object.keys(ids).length > 0) {
    const logData = sanitizedData !== undefined ? { ...sanitizedData, ...ids } : ids;
    console.log(`${styledPrefix} ${message}`, logData);
  } else {
    if (sanitizedData !== undefined) {
      console.log(`${styledPrefix} ${message}`, sanitizedData);
    } else {
      console.log(`${styledPrefix} ${message}`);
    }
  }
}

export function createLogger(level: LogLevel, prefix: string) {
  const hash = simpleHash(prefix);
  const color = COLORS[hash % COLORS.length];

  // Use plain prefix in production, styled prefix otherwise
  const styledPrefix =
    process.env.NODE_ENV === "production"
      ? `[${prefix}]`
      : `${BOLD}${color}[${prefix}]${RESET}`;

  // In production, only allow warn and error logs
  const isProduction = process.env.NODE_ENV === "production";

  return {
    debug: (message: string, data?: any) => {
      if (!isProduction && level === LogLevel.DEBUG) {
        logWithOptionalIds(styledPrefix, message, data);
      }
    },
    info: (message: string, data?: any) => {
      if (
        !isProduction &&
        (level === LogLevel.INFO || level === LogLevel.DEBUG)
      ) {
        logWithOptionalIds(styledPrefix, message, data);
      }
    },
    warn: (message: string, data?: any) => {
      if (
        level === LogLevel.WARN ||
        level === LogLevel.INFO ||
        level === LogLevel.DEBUG
      ) {
        logWithOptionalIds(styledPrefix, message, data);
      }
    },
    error: (message: string, data?: any) => {
      if (
        level === LogLevel.ERROR ||
        level === LogLevel.WARN ||
        level === LogLevel.INFO ||
        level === LogLevel.DEBUG
      ) {
        logWithOptionalIds(styledPrefix, message, data);
      }
    },
  };
}
