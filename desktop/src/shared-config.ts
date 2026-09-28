import {
  chmodSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

/** The one config file the packaged desktop app and the `oswe` CLI share. */
export interface SharedConfig {
  backendUrl: string | null;
  /** CLI sessions by backend origin: a session only works on the backend that minted it. */
  sessions: Record<string, string>;
}

export const DEFAULT_DEVELOPMENT_BACKEND_URL = "http://localhost:2024";

const DIR_MODE = 0o700;
const FILE_MODE = 0o600;

export function sharedConfigPath(home: string): string {
  return join(home, ".open-swe", "config.json");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readText(path: string): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch (error) {
    if (isRecord(error) && error["code"] === "ENOENT") return null;
    throw error;
  }
}

function parseObject(path: string, text: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error(`${path} is not valid JSON`, { cause: error });
  }
  if (!isRecord(parsed)) throw new Error(`${path} is not a JSON object`);
  return parsed;
}

export function readSharedConfig(path: string): SharedConfig {
  const text = readText(path);
  if (text === null) return { backendUrl: null, sessions: {} };
  const parsed = parseObject(path, text);
  const backendUrl = parsed["backendUrl"];
  const rawSessions = parsed["sessions"];
  const sessions: Record<string, string> = {};
  if (isRecord(rawSessions)) {
    for (const [backend, session] of Object.entries(rawSessions)) {
      if (typeof session === "string") sessions[backend] = session;
    }
  }
  return {
    backendUrl: typeof backendUrl === "string" ? backendUrl : null,
    sessions,
  };
}

export function writeSharedConfig(path: string, config: SharedConfig): void {
  const directory = dirname(path);
  mkdirSync(directory, { recursive: true, mode: DIR_MODE });
  chmodSync(directory, DIR_MODE);
  writeFileSync(path, `${JSON.stringify(config, null, 2)}\n`, {
    mode: FILE_MODE,
  });
  chmodSync(path, FILE_MODE);
}

export function updateSharedConfig(
  path: string,
  update: (config: SharedConfig) => SharedConfig,
): SharedConfig {
  const next = update(readSharedConfig(path));
  writeSharedConfig(path, next);
  return next;
}

/** Move a desktop-only `desktop-config.json` into the shared file; a backend already shared wins. */
export function migrateDesktopConfig(legacyPath: string, path: string): void {
  const text = readText(legacyPath);
  if (text === null) return;
  const legacyUrl = parseObject(legacyPath, text)["backendUrl"];
  updateSharedConfig(path, (config) => ({
    ...config,
    backendUrl:
      config.backendUrl ?? (typeof legacyUrl === "string" ? legacyUrl : null),
  }));
  rmSync(legacyPath);
}
