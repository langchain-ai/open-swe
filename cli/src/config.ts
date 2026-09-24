import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises"
import { homedir, platform } from "node:os"
import { join } from "node:path"

import { normalizeBackend } from "./api.ts"
import { resolveCredential, type Credential } from "./credentials.ts"
import { errorCode, isRecord, parseJson, stringAt } from "./json.ts"

/** ``$HOME`` wins so a test, or a sandboxed run, can point at its own home. */
function home(): string {
  return process.env["HOME"] || homedir()
}

function configDir(): string {
  return join(home(), ".open-swe")
}

function configFile(): string {
  return join(configDir(), "config.json")
}

function bridgesFile(): string {
  return join(configDir(), "bridges.json")
}

const DIR_MODE = 0o700
const FILE_MODE = 0o600

/** The backend the desktop app falls back to when nothing names one. */
const DEVELOPMENT_BACKEND_URL = "http://localhost:2024"

export interface CliConfig {
  backend: string
  session: string
}

export interface RunConfig {
  backend: string
  credential: Credential
}

/** Bridges this machine has already created, so a directory reopens its own. */
export interface BridgeMemory {
  roots: Record<string, string>
  threads: Record<string, string>
}

async function readFileOrNull(path: string): Promise<string | null> {
  try {
    return await readFile(path, "utf8")
  } catch (cause) {
    if (errorCode(cause) === "ENOENT") return null
    throw cause
  }
}

async function writePrivate(path: string, value: unknown): Promise<void> {
  await mkdir(configDir(), { recursive: true, mode: DIR_MODE })
  await chmod(configDir(), DIR_MODE)
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, {
    mode: FILE_MODE,
  })
  await chmod(path, FILE_MODE)
}

function stringMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {}
  const out: Record<string, string> = {}
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "string") out[key] = entry
  }
  return out
}

/** Where the desktop app keeps the backend URL it was pointed at. */
function desktopConfigPaths(): string[] {
  const base = home()
  const names = ["Open SWE", "Open SWE Development"]
  const roots =
    platform() === "darwin"
      ? [join(base, "Library", "Application Support")]
      : platform() === "win32"
        ? [join(base, "AppData", "Roaming")]
        : [process.env["XDG_CONFIG_HOME"] || join(base, ".config")]
  return roots.flatMap((root) =>
    names.map((name) => join(root, name, "desktop-config.json"))
  )
}

async function desktopBackend(): Promise<string | null> {
  for (const path of desktopConfigPaths()) {
    const text = await readFileOrNull(path)
    if (text === null) continue
    const parsed = parseJson(text)
    const url = isRecord(parsed) ? stringAt(parsed, "backendUrl") : null
    if (url) return url
  }
  return null
}

async function storedConfig(): Promise<Partial<CliConfig>> {
  const text = await readFileOrNull(configFile())
  if (text === null) return {}
  const parsed = parseJson(text)
  if (!isRecord(parsed)) return {}
  return {
    backend: stringAt(parsed, "backend") ?? undefined,
    session: stringAt(parsed, "session") ?? undefined,
  }
}

/**
 * The backend to talk to, resolved the way the desktop app resolves its own:
 * the environment first, under the same variable names, then what was stored,
 * then the development default.
 */
export async function readBackend(): Promise<string> {
  const env = process.env
  return (
    env["OPEN_SWE_BACKEND_URL"] ||
    env["OPEN_SWE_DESKTOP_URL"] ||
    (await storedConfig()).backend ||
    (await desktopBackend()) ||
    DEVELOPMENT_BACKEND_URL
  )
}

/**
 * Where the CLI is pointed and who it is.
 *
 * A session has no desktop fallback: the app keeps it in an encrypted cookie
 * store no other process can read, so it comes from `OPEN_SWE_SESSION` or from
 * `oswe login`.
 */
export async function readConfig(): Promise<RunConfig | null> {
  const backend = normalizeBackend(await readBackend())
  const credential = resolveCredential(backend, (await storedConfig()).session)
  return credential === null ? null : { backend, credential }
}

export async function writeConfig(config: CliConfig): Promise<string> {
  await writePrivate(configFile(), config)
  return configFile()
}

export async function clearConfig(): Promise<boolean> {
  try {
    await rm(configFile())
    return true
  } catch (cause) {
    if (errorCode(cause) === "ENOENT") return false
    throw cause
  }
}

export async function readBridgeMemory(): Promise<BridgeMemory> {
  const text = await readFileOrNull(bridgesFile())
  const parsed = text === null ? null : parseJson(text)
  if (!isRecord(parsed)) return { roots: {}, threads: {} }
  return {
    roots: stringMap(parsed["roots"]),
    threads: stringMap(parsed["threads"]),
  }
}

export async function writeBridgeMemory(memory: BridgeMemory): Promise<void> {
  await writePrivate(bridgesFile(), memory)
}

export async function rememberBridge(options: {
  root: string
  bridgeId: string
  threadId: string | null
}): Promise<void> {
  const memory = await readBridgeMemory()
  memory.roots[options.root] = options.bridgeId
  if (options.threadId) memory.threads[options.threadId] = options.bridgeId
  await writeBridgeMemory(memory)
}
