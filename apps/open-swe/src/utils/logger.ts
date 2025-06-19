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

export function createLogger(level: LogLevel, prefix: string) {
  const hash = simpleHash(prefix);
  const color = COLORS[hash % COLORS.length];
  const styledPrefix = `${BOLD}${color}[${prefix}]${RESET}`; // Apply bold and color

  return {
    debug: (message: string, data?: any) => {
      if (level === LogLevel.DEBUG) {
        if (data !== undefined) {
          console.log(`${styledPrefix} ${message}`, data);
        } else {
          console.log(`${styledPrefix} ${message}`);
        }
      }
    },
    info: (message: string, data?: any) => {
      if (level === LogLevel.INFO || level === LogLevel.DEBUG) {
        if (data !== undefined) {
          console.log(`${styledPrefix} ${message}`, data);
        } else {
          console.log(`${styledPrefix} ${message}`);
        }
      }
    },
    warn: (message: string, data?: any) => {
      if (
        level === LogLevel.WARN ||
        level === LogLevel.INFO ||
        level === LogLevel.DEBUG
      ) {
        if (data !== undefined) {
          console.log(`${styledPrefix} ${message}`, data);
        } else {
          console.log(`${styledPrefix} ${message}`);
        }
      }
    },
    error: (message: string, data?: any) => {
      if (
        level === LogLevel.ERROR ||
        level === LogLevel.WARN ||
        level === LogLevel.INFO ||
        level === LogLevel.DEBUG
      ) {
        if (data !== undefined) {
          console.log(`${styledPrefix} ${message}`, data);
        } else {
          console.log(`${styledPrefix} ${message}`);
        }
      }
    },
  };
}

