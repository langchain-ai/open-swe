import { chmod, mkdir, readFile, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

import {
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  readSharedConfig,
  sharedConfigPath,
  updateSharedConfig,
} from "../../desktop/src/shared-config.ts"
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
  return sharedConfigPath(home())
}

function bridgesFile(): string {
  return join(configDir(), "bridges.json")
}

const DIR_MODE = 0o700
const FILE_MODE = 0o600

export interface RunConfig {
  backend: string
  credential: Credential
}

/** The bridge a thread this machine started is bound to, and the directory it serves. */
export interface ThreadBridge {
  bridgeId: string
  root: string
}

/** Threads this machine started, by id, so `--thread` can reopen their bridge. */
export type BridgeMemory = Record<string, ThreadBridge>

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

/**
 * The backend to talk to: the environment first, under the desktop app's own
 * variable names, then the backend the CLI and the desktop app share, then the
 * development default.
 */
export async function readBackend(): Promise<string> {
  const env = process.env
  return (
    env["OPEN_SWE_BACKEND_URL"] ||
    env["OPEN_SWE_DESKTOP_URL"] ||
    readSharedConfig(configFile()).backendUrl ||
    DEFAULT_DEVELOPMENT_BACKEND_URL
  )
}

/**
 * Where the CLI is pointed and who it is.
 *
 * The desktop app keeps its own session in an encrypted cookie store no other
 * process can read, so the CLI's comes from `OPEN_SWE_SESSION` or `oswe login`.
 */
export async function readConfig(): Promise<RunConfig | null> {
  const backend = normalizeBackend(await readBackend())
  const session = readSharedConfig(configFile()).sessions[backend]
  const credential = resolveCredential(
    backend,
    session ? { session, path: configFile() } : null
  )
  return credential === null ? null : { backend, credential }
}

/** Store a session for `backend`, and make it the shared backend when `select` is set. */
export async function storeSession(
  backend: string,
  session: string,
  select: boolean
): Promise<string> {
  updateSharedConfig(configFile(), (config) => ({
    backendUrl: select ? backend : config.backendUrl,
    sessions: { ...config.sessions, [backend]: session },
  }))
  return configFile()
}

export async function forgetSession(backend: string): Promise<boolean> {
  const { sessions } = readSharedConfig(configFile())
  if (!(backend in sessions)) return false
  updateSharedConfig(configFile(), (config) => ({
    ...config,
    sessions: Object.fromEntries(
      Object.entries(config.sessions).filter(([key]) => key !== backend)
    ),
  }))
  return true
}

export async function readBridgeMemory(): Promise<BridgeMemory> {
  const text = await readFileOrNull(bridgesFile())
  const parsed = text === null ? null : parseJson(text)
  const threads = isRecord(parsed) ? parsed["threads"] : null
  const memory: BridgeMemory = {}
  if (!isRecord(threads)) return memory
  for (const [threadId, entry] of Object.entries(threads)) {
    const record = isRecord(entry) ? entry : null
    const bridgeId = stringAt(record, "bridgeId")
    const root = stringAt(record, "root")
    if (bridgeId !== null && root !== null)
      memory[threadId] = { bridgeId, root }
  }
  return memory
}

export async function rememberThreadBridge(
  threadId: string,
  bridge: ThreadBridge
): Promise<void> {
  const memory = await readBridgeMemory()
  memory[threadId] = bridge
  await writePrivate(bridgesFile(), { threads: memory })
}
