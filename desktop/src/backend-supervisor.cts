import { spawn as spawnProcess } from "node:child_process"
import { randomBytes } from "node:crypto"
import fs from "node:fs"
import http from "node:http"
import path from "node:path"
import type { Readable } from "node:stream"

const HOST = "127.0.0.1"
const START_TIMEOUT_MS = 60_000
const STOP_TIMEOUT_MS = 5_000
const THREAD_STATUS = { busy: "running", error: "error" } as const
const PROVIDER_KEYS: Readonly<Record<string, readonly string[]>> = {
  anthropic: ["ANTHROPIC_API_KEY"],
  fireworks: ["FIREWORKS_API_KEY"],
  google_genai: ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
  openai: ["OPENAI_API_KEY"],
}

interface BackendTarget {
  command: string
  args: string[]
  cwd: string
}

interface DevBackendOptions {
  repoRoot: string
  port: number
  stateDir?: string
  env?: NodeJS.ProcessEnv
}

interface PackagedBackendOptions {
  resourcesPath: string
  port: number
  stateDir?: string
  platform?: NodeJS.Platform
}

type LocalBackendOptions =
  | ({ isPackaged: true } & PackagedBackendOptions)
  | ({ isPackaged?: false } & DevBackendOptions)

interface BackendChild {
  stdout?: Readable | null
  stderr?: Readable | null
  exitCode?: number | null
  signalCode?: NodeJS.Signals | null
  once(event: "error", listener: (error: Error) => void): this
  once(event: "exit", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this
  kill(signal?: NodeJS.Signals): boolean
}

type SpawnBackend = (
  command: string,
  args: readonly string[],
  options: {
    cwd: string
    env: NodeJS.ProcessEnv
    stdio: ["ignore", "pipe", "pipe"]
    windowsHide: boolean
  }
) => BackendChild

type FetchBackend = (
  input: string | URL | Request,
  init?: RequestInit & { duplex?: "half" }
) => Promise<Response>

interface BackendSupervisorOptions {
  isPackaged?: boolean
  repoRoot?: string
  resourcesPath?: string
  projectsFile?: string
  stateDir?: string
  env?: NodeJS.ProcessEnv
  providerEnv?: () => NodeJS.ProcessEnv
  openAiOAuthAvailable?: () => boolean
  startTimeoutMs?: number
  stopTimeoutMs?: number
  spawn?: SpawnBackend
  fetch?: FetchBackend
  reservePort?: (host?: string) => Promise<number>
}

interface GraphRuntimeConfig {
  origin: string
  token: string
}

interface CredentialStatus {
  available: boolean
  variable: string | null
  canSignIn?: true
}

export function devBackendTarget({
  repoRoot,
  port,
  stateDir,
  env = process.env,
}: DevBackendOptions): BackendTarget {
  return {
    command:
      env.OPEN_SWE_LOCAL_BACKEND_COMMAND ||
      path.join(repoRoot, "desktop", "node_modules", "node", "bin", "node"),
    args: [
      env.OPEN_SWE_LOCAL_BACKEND_SCRIPT ||
        path.join(repoRoot, "apps", "graphs", "dist", "server.js"),
      "--host",
      HOST,
      "--port",
      String(port),
      "--state-dir",
      stateDir || path.join(repoRoot, ".local-graph-state"),
      ...(env.OPEN_SWE_LOCAL_GRAPH_ENTRYPOINT
        ? ["--graph-entrypoint", env.OPEN_SWE_LOCAL_GRAPH_ENTRYPOINT]
        : []),
    ],
    cwd: repoRoot,
  }
}

export function packagedBackendTarget({
  resourcesPath,
  port,
  stateDir,
  platform = process.platform,
}: PackagedBackendOptions): BackendTarget {
  const root = path.join(resourcesPath, "local-backend")
  const executable = path.join(
    root,
    "runtime",
    platform === "win32" ? "node.exe" : "bin/node"
  )
  return {
    command: executable,
    args: [
      path.join(root, "dist", "server.js"),
      "--host",
      HOST,
      "--port",
      String(port),
      "--state-dir",
      stateDir || root,
    ],
    cwd: stateDir || root,
  }
}

export function localBackendTarget(options: LocalBackendOptions): BackendTarget {
  if (options.isPackaged === true) return packagedBackendTarget(options)
  return devBackendTarget(options)
}

export function reservePort(host = HOST): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const server = http.createServer()
    server.unref()
    server.once("error", reject)
    server.listen(0, host, () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : null
      server.close((error) =>
        error || !port ? reject(error || new Error("No port")) : resolve(port)
      )
    })
  })
}

function delay(milliseconds: number): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds))
}

export function modelCredentialStatus(
  modelId: unknown,
  env: NodeJS.ProcessEnv,
  options: { openAiOAuth?: boolean } = {}
): CredentialStatus {
  const provider = typeof modelId === "string" ? modelId.split(":", 1)[0] : ""
  const variables = PROVIDER_KEYS[provider]
  if (!variables) return { available: true, variable: null }
  const variable = variables.find((key) => env[key])
  const oauthAvailable = provider === "openai" && options.openAiOAuth === true
  return {
    available: Boolean(variable) || oauthAvailable,
    variable: variable || (oauthAvailable ? null : variables[0]),
    ...(provider === "openai" && !variable ? { canSignIn: true as const } : {}),
  }
}

export class BackendSupervisor {
  readonly options: BackendSupervisorOptions
  spawn: SpawnBackend
  fetch: FetchBackend
  readonly reservePort: (host?: string) => Promise<number>
  child: BackendChild | null = null
  port: number | null = null
  token: string | null = null
  logs = ""
  closing = false
  ready: Promise<GraphRuntimeConfig> | null = null
  failure: Error | null = null

  constructor(options: BackendSupervisorOptions = {}) {
    this.options = options
    this.spawn = options.spawn || (spawnProcess as SpawnBackend)
    this.fetch = options.fetch || (fetch as FetchBackend)
    this.reservePort = options.reservePort || reservePort
  }

  start(): Promise<GraphRuntimeConfig> {
    if (this.ready && this.child && !this.failure) return this.ready
    this.ready = this.startOnce().catch((error: unknown) => {
      this.ready = null
      throw error
    })
    return this.ready
  }

  private async startOnce(): Promise<GraphRuntimeConfig> {
    this.closing = false
    this.failure = null
    this.logs = ""
    this.port = await this.reservePort(HOST)
    this.token = randomBytes(32).toString("base64url")
    const target = this.options.isPackaged
      ? packagedBackendTarget({
          resourcesPath: this.options.resourcesPath || "",
          port: this.port,
          stateDir: this.options.stateDir,
        })
      : devBackendTarget({
          repoRoot: this.options.repoRoot || process.cwd(),
          port: this.port,
          stateDir: this.options.stateDir,
          env: this.options.env,
        })
    if (!this.options.projectsFile) throw new Error("Local project allowlist is not configured")
    if (this.options.stateDir) fs.mkdirSync(this.options.stateDir, { recursive: true })
    if (this.options.isPackaged && !fs.existsSync(target.command)) {
      throw new Error(`Bundled local backend is missing: ${target.command}`)
    }
    const child = this.spawn(target.command, target.args, {
      cwd: target.cwd,
      env: {
        ...process.env,
        ...this.options.env,
        ...(this.options.providerEnv?.() || {}),
        OPEN_SWE_LOCAL_AUTH_TOKEN: this.token,
        OPEN_SWE_LOCAL_PROJECTS_FILE: this.options.projectsFile,
        ...(this.options.stateDir
          ? { OPEN_SWE_LOCAL_ARTIFACTS_DIR: path.join(this.options.stateDir, "artifacts") }
          : {}),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    })
    this.child = child
    const append = (chunk: Buffer | string): void => {
      this.logs = `${this.logs}${chunk.toString()}`.slice(-16_000)
      if (process.env.OPEN_SWE_LOCAL_SERVER_LOGS === "1") {
        process.stderr.write(`[local-graph] ${chunk.toString()}`)
      }
    }
    child.stdout?.on("data", append)
    child.stderr?.on("data", append)

    let startupError: Error | null = null
    const exited = new Promise<void>((resolve) => {
      child.once("error", (error) => {
        startupError = error
        if (!this.closing) this.failure = error
        resolve()
      })
      child.once("exit", (code, signal) => {
        if (!startupError) {
          const reason = signal ? `signal ${signal}` : `exit code ${code}`
          startupError = new Error(`Local LangGraph backend stopped with ${reason}`)
        }
        if (!this.closing) this.failure = startupError
        resolve()
      })
    })
    const deadline = Date.now() + (this.options.startTimeoutMs || START_TIMEOUT_MS)
    while (Date.now() < deadline) {
      if (startupError) break
      try {
        const response = await this.fetch(`http://${HOST}:${this.port}/assistants/search`, {
          method: "POST",
          headers: {
            authorization: `Bearer ${this.token}`,
            "content-type": "application/json",
          },
          body: "{}",
          signal: AbortSignal.timeout(1_000),
        })
        if (response.ok) {
          this.failure = null
          return this.runtimeConfig()
        }
      } catch {}
      await Promise.race([delay(150), exited])
    }
    await this.close()
    const detail = this.logs.trim()
    if (startupError) {
      const error = startupError as Error
      throw new Error(`${error.message}${detail ? `\n${detail}` : ""}`)
    }
    throw new Error(`Local LangGraph backend did not become healthy${detail ? `\n${detail}` : ""}`)
  }

  credentialStatus(modelId: unknown): CredentialStatus {
    return modelCredentialStatus(
      modelId,
      { ...process.env, ...this.options.env },
      { openAiOAuth: this.options.openAiOAuthAvailable?.() === true }
    )
  }

  private runtimeConfig(): GraphRuntimeConfig {
    if (!this.port || !this.token) throw new Error("Local graph runtime is not ready")
    return { origin: `http://${HOST}:${this.port}`, token: this.token }
  }

  async request(
    pathname: string,
    init: RequestInit & { duplex?: "half" } = {}
  ): Promise<Response> {
    await this.start()
    const headers = new Headers(init.headers)
    headers.set("authorization", `Bearer ${this.token}`)
    headers.set("accept-encoding", "identity")
    return this.fetch(`http://${HOST}:${this.port}${pathname}`, { ...init, headers })
  }

  async threadActivity(): Promise<Record<string, "running" | "error"> | null> {
    if (!this.child || !this.port || !this.token) return {}
    try {
      const response = await this.fetch(`http://${HOST}:${this.port}/threads/search`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${this.token}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ limit: 1_000 }),
        signal: AbortSignal.timeout(2_000),
      })
      if (!response.ok) return null
      const threads: unknown = await response.json()
      if (!Array.isArray(threads)) return null
      const activity: Record<string, "running" | "error"> = {}
      for (const thread of threads) {
        if (!thread || typeof thread !== "object") continue
        const value = thread as { thread_id?: unknown; status?: keyof typeof THREAD_STATUS }
        const status = value.status ? THREAD_STATUS[value.status] : undefined
        if (status && typeof value.thread_id === "string") activity[value.thread_id] = status
      }
      return activity
    } catch {
      return null
    }
  }

  async createThread(threadId: string): Promise<void> {
    const response = await this.request("/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        if_exists: "do_nothing",
        metadata: { graph_id: "agent" },
      }),
    })
    if (!response.ok) {
      throw new Error(`Could not create local LangGraph thread (${response.status})`)
    }
  }

  async deleteThread(threadId: string): Promise<void> {
    const response = await this.request(`/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    })
    if (!response.ok && response.status !== 404) {
      throw new Error(`Could not delete local LangGraph thread (${response.status})`)
    }
  }

  async close(): Promise<void> {
    if (this.closing) return
    this.closing = true
    const child = this.child
    this.child = null
    this.port = null
    this.token = null
    this.ready = null
    this.failure = null
    if (!child || child.exitCode != null || child.signalCode != null) return
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        try {
          child.kill("SIGKILL")
        } catch {}
        resolve()
      }, this.options.stopTimeoutMs || STOP_TIMEOUT_MS)
      timer.unref?.()
      child.once("exit", () => {
        clearTimeout(timer)
        resolve()
      })
      try {
        child.kill("SIGTERM")
      } catch {
        clearTimeout(timer)
        resolve()
      }
    })
  }
}
