import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

export interface MigrationHost {
  home: string;
  platform: NodeJS.Platform;
  env: Record<string, string | undefined>;
}

/** Electron's default userData folder for the packaged app, named after `desktop/package.json`. */
export function legacyDesktopConfigPath({
  home,
  platform,
  env,
}: MigrationHost): string {
  const appData =
    platform === "darwin"
      ? join(home, "Library", "Application Support")
      : platform === "win32"
        ? env["APPDATA"] || join(home, "AppData", "Roaming")
        : env["XDG_CONFIG_HOME"] || join(home, ".config");
  return join(appData, "open-swe-desktop", "desktop-config.json");
}

/**
 * Copy the backend a packaged desktop build kept in `desktop-config.json` into
 * the shared file, unless the shared file already names one. Whichever of the
 * desktop app and the CLI runs first does it. The old file stays, because a
 * desktop build from before the shared file only reads that one.
 */
export function migrateDesktopConfig(host: MigrationHost): void {
  const path = sharedConfigPath(host.home);
  if (readSharedConfig(path).backendUrl !== null) return;
  const legacyPath = legacyDesktopConfigPath(host);
  const text = readText(legacyPath);
  if (text === null) return;
  const legacyUrl = parseObject(legacyPath, text)["backendUrl"];
  if (typeof legacyUrl !== "string") return;
  updateSharedConfig(path, (config) => ({
    ...config,
    backendUrl: config.backendUrl ?? legacyUrl,
  }));
}
