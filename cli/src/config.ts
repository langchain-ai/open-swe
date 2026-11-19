import { homedir, platform } from "node:os"

import {
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  migrateDesktopConfig,
  readSharedConfig,
  sessionKey,
  shareSession,
  sharedConfigPath,
  unshareSession,
  updateSharedConfig,
  type SharedConfig,
} from "../../desktop/src/shared-config.ts"
import { normalizeBackend } from "./api.ts"
import { resolveCredential, type Credential } from "./credentials.ts"

/** ``$HOME`` wins so a test, or a sandboxed run, can point at its own home. */
function home(): string {
  return process.env["HOME"] || homedir()
}

function configFile(): string {
  return sharedConfigPath(home())
}

function sharedConfig(): SharedConfig {
  migrateDesktopConfig({ home: home(), platform: platform(), env: process.env })
  return readSharedConfig(configFile())
}

export interface RunConfig {
  backend: string
  credential: Credential
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
    sharedConfig().backendUrl ||
    DEFAULT_DEVELOPMENT_BACKEND_URL
  )
}

/** Where the CLI is pointed and who it is; the desktop app shares its session here while signed in. */
export async function readConfig(): Promise<RunConfig | null> {
  const backend = normalizeBackend(await readBackend())
  const session = sharedConfig().sessions[sessionKey(backend)]
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
  shareSession(configFile(), backend, session)
  if (select)
    updateSharedConfig(configFile(), (config) => ({
      ...config,
      backendUrl: backend,
    }))
  return configFile()
}

export async function forgetSession(backend: string): Promise<boolean> {
  return unshareSession(configFile(), backend)
}
