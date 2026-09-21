import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

import { errorCode, isRecord, parseJson, stringAt } from "./json.ts"

const CONFIG_DIR = join(homedir(), ".open-swe")
const CONFIG_FILE = join(CONFIG_DIR, "config.json")
const BRIDGES_FILE = join(CONFIG_DIR, "bridges.json")

const DIR_MODE = 0o700
const FILE_MODE = 0o600

export interface CliConfig {
  backend: string
  session: string
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
  await mkdir(CONFIG_DIR, { recursive: true, mode: DIR_MODE })
  await chmod(CONFIG_DIR, DIR_MODE)
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

export async function readConfig(): Promise<CliConfig | null> {
  const text = await readFileOrNull(CONFIG_FILE)
  if (text === null) return null
  const parsed = parseJson(text)
  if (!isRecord(parsed)) return null
  const backend = stringAt(parsed, "backend")
  const session = stringAt(parsed, "session")
  if (!backend || !session) return null
  return { backend, session }
}

export async function writeConfig(config: CliConfig): Promise<string> {
  await writePrivate(CONFIG_FILE, config)
  return CONFIG_FILE
}

export async function clearConfig(): Promise<boolean> {
  try {
    await rm(CONFIG_FILE)
    return true
  } catch (cause) {
    if (errorCode(cause) === "ENOENT") return false
    throw cause
  }
}

export async function readBridgeMemory(): Promise<BridgeMemory> {
  const text = await readFileOrNull(BRIDGES_FILE)
  const parsed = text === null ? null : parseJson(text)
  if (!isRecord(parsed)) return { roots: {}, threads: {} }
  return {
    roots: stringMap(parsed["roots"]),
    threads: stringMap(parsed["threads"]),
  }
}

export async function writeBridgeMemory(memory: BridgeMemory): Promise<void> {
  await writePrivate(BRIDGES_FILE, memory)
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

export const configPath = CONFIG_FILE
export const bridgesPath = BRIDGES_FILE
