/**
 * Diagnostics for installed builds: a bounded log of the renderer's console
 * output plus the main process's own warnings, packaged into one report a
 * user can save and send when something goes wrong. Nothing is collected
 * beyond what the app already prints to its console.
 */

import type { WebContentsConsoleMessageEventParams } from "electron";

export type ConsoleLevel = "debug" | "info" | "warning" | "error";

export interface ConsoleEntry {
  /** ISO timestamp. */
  at: string;
  level: ConsoleLevel;
  message: string;
  source: string | null;
}

export interface DiagnosticsAppInfo {
  name: string;
  version: string;
  isPackaged: boolean;
  electron: string;
  chrome: string;
  node: string;
  platform: string;
  arch: string;
  osRelease: string;
  locale: string;
}

export interface DiagnosticsReportInput {
  app: DiagnosticsAppInfo;
  /** Host of the configured backend, never the full URL or a session. */
  backendHost: string | null;
  renderer: ReadonlyArray<ConsoleEntry>;
  main: ReadonlyArray<ConsoleEntry>;
  /** `window.__openSwePerf.export()` from the renderer, when available. */
  perf: string | null;
}

export function normalizeConsoleMessage(
  details: WebContentsConsoleMessageEventParams,
): ConsoleEntry {
  const line = details.lineNumber > 0 ? `:${details.lineNumber}` : "";
  return {
    at: new Date().toISOString(),
    level: details.level,
    message: details.message,
    source: details.sourceId
      ? `${redactSecrets(details.sourceId)}${line}`
      : null,
  };
}

/** Newest entries win; the oldest fall off once the limit is reached. */
export class ConsoleLogBuffer {
  private readonly items: Array<ConsoleEntry> = [];

  constructor(private readonly limit: number) {}

  push(entry: ConsoleEntry): void {
    this.items.push(entry);
    if (this.items.length > this.limit) this.items.shift();
  }

  entries(): ReadonlyArray<ConsoleEntry> {
    return this.items;
  }
}

const MAIN_LEVELS: Record<"log" | "info" | "warn" | "error", ConsoleLevel> = {
  log: "info",
  info: "info",
  warn: "warning",
  error: "error",
};

function formatArgument(value: unknown): string {
  if (value instanceof Error) return value.stack ?? value.message;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Mirror the main process's console into a buffer without silencing it. */
export function captureProcessConsole(buffer: ConsoleLogBuffer): void {
  for (const method of Object.keys(MAIN_LEVELS) as Array<
    keyof typeof MAIN_LEVELS
  >) {
    const original = console[method];
    console[method] = (...args: Array<unknown>) => {
      buffer.push({
        at: new Date().toISOString(),
        level: MAIN_LEVELS[method],
        message: args.map(formatArgument).join(" "),
        source: "main",
      });
      original.apply(console, args);
    };
  }
}

const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/(osw_session=)[^;&\s"']+/gi, "$1[redacted]"],
  [/(authorization:?\s*bearer\s+)[^\s"']+/gi, "$1[redacted]"],
  [
    /([?&](?:token|access_token|code|state|api_key|apikey|key)=)[^&\s"']+/gi,
    "$1[redacted]",
  ],
  [
    /\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/g,
    "[redacted]",
  ],
  [
    /\b(sk-[A-Za-z0-9_-]{16,}|lsv2_[A-Za-z0-9_]{16,}|xox[abprs]-[A-Za-z0-9-]{10,})\b/g,
    "[redacted]",
  ],
];

/** Best-effort scrub of session cookies, bearer tokens and provider keys. */
export function redactSecrets(text: string): string {
  let result = text;
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    result = result.replace(pattern, replacement);
  }
  return result;
}

function formatEntries(entries: ReadonlyArray<ConsoleEntry>): string {
  if (entries.length === 0) return "(empty)";
  return entries
    .map((entry) => {
      const source = entry.source ? ` ${entry.source}` : "";
      return `${entry.at} ${entry.level.padEnd(7)}${source}\n  ${redactSecrets(entry.message).replace(/\n/g, "\n  ")}`;
    })
    .join("\n");
}

export function buildDiagnosticsReport(input: DiagnosticsReportInput): string {
  const { app } = input;
  return [
    `${app.name} diagnostics report`,
    `Generated: ${new Date().toISOString()}`,
    "",
    "## App",
    `version: ${app.version}${app.isPackaged ? "" : " (development)"}`,
    `electron: ${app.electron}  chrome: ${app.chrome}  node: ${app.node}`,
    `platform: ${app.platform} ${app.arch}  os: ${app.osRelease}  locale: ${app.locale}`,
    `backend: ${input.backendHost ?? "(not configured)"}`,
    "",
    `## Renderer console (${input.renderer.length} entries, newest last)`,
    formatEntries(input.renderer),
    "",
    `## Main process console (${input.main.length} entries, newest last)`,
    formatEntries(input.main),
    "",
    "## Performance spans",
    input.perf ? redactSecrets(input.perf) : "(none recorded)",
    "",
  ].join("\n");
}

export function diagnosticsFileName(): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `open-swe-diagnostics-${stamp}.txt`;
}
