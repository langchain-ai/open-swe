import { spawn as spawnProcess } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import type { Readable } from "node:stream"

import { reservePort } from "./backend-supervisor.cjs"

const HOST = "127.0.0.1"
const START_TIMEOUT_MS = 60_000
const STOP_TIMEOUT_MS = 5_000

interface ApplicationTarget {
  command: string
  args: string[]
  cwd: string
}

interface GraphRuntime {
  start(): Promise<{ origin: string; token: string }>
}

interface ApplicationChild {
  stdout?: Readable | null
  stderr?: Readable | null
  exitCode?: number | null
  signalCode?: NodeJS.Signals | null
  once(event: "error", listener: (error: Error) => void): this
  once(event: "exit", listener: (code: number | null, signal: NodeJS.Signals | null) => void): this
  kill(signal?: NodeJS.Signals): boolean
}

type SpawnApplication = (
  command: string,
  args: readonly string[],
  options: {
    cwd: string
    env: NodeJS.ProcessEnv
    stdio: ["ignore", "pipe", "pipe"]
    windowsHide: boolean
  }
) => ApplicationChild

interface ApplicationSupervisorOptions {
  graph: GraphRuntime
  isPackaged?: boolean
  repoRoot?: string
  resourcesPath?: string
  backendUrl?: string | null
  env?: NodeJS.ProcessEnv
  startTimeoutMs?: number
  stopTimeoutMs?: number
  spawn?: SpawnApplication
  fetch?: typeof fetch
  reservePort?: (host?: string) => Promise<number>
}

export function devApplicationTarget(repoRoot: string): ApplicationTarget {
  const outputRoot = path.join(repoRoot, "ui", ".output")
  return {
    command: path.join(repoRoot, "desktop", "node_modules", "node", "bin", "node"),
    args: [path.join(outputRoot, "server", "index.mjs")],
    cwd: outputRoot,
  }
}

export function packagedApplicationTarget(resourcesPath: string): ApplicationTarget {
  const outputRoot = path.join(resourcesPath, "ui")
  return {
    command: path.join(resourcesPath, "local-backend", "runtime", "bin", "node"),
    args: [path.join(outputRoot, "server", "index.mjs")],
    cwd: outputRoot,
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds))
}

export class ApplicationSupervisor {
  private readonly options: ApplicationSupervisorOptions
  private readonly spawn: SpawnApplication
  private readonly fetch: typeof fetch
  private readonly reservePort: (host?: string) => Promise<number>
  private child: ApplicationChild | null = null
  private ready: Promise<{ appUrl: string }> | null = null
  private closing = false
  private logs = ""
  private appUrl: string | null = null
  private backendUrl: string | null

  constructor(options: ApplicationSupervisorOptions) {
    this.options = options
    this.spawn = options.spawn || (spawnProcess as SpawnApplication)
    this.fetch = options.fetch || fetch
    this.reservePort = options.reservePort || reservePort
    this.backendUrl = options.backendUrl || null
  }

  start(): Promise<{ appUrl: string }> {
    if (this.ready && this.child) return this.ready
    this.ready = this.startOnce().catch((error: unknown) => {
      this.ready = null
      throw error
    })
    return this.ready
  }

  async setBackendUrl(backendUrl: string | null): Promise<void> {
    if (this.backendUrl === backendUrl) return
    this.backendUrl = backendUrl
    await this.close()
  }

  private async startOnce(): Promise<{ appUrl: string }> {
    this.closing = false
    this.logs = ""
    const graph = await this.options.graph.start()
    const port = await this.reservePort(HOST)
    this.appUrl = `http://${HOST}:${port}`
    const target = this.options.isPackaged
      ? packagedApplicationTarget(this.options.resourcesPath || "")
      : devApplicationTarget(this.options.repoRoot || process.cwd())
    if (!fs.existsSync(target.command) || !fs.existsSync(target.args[0]!)) {
      throw new Error(`TanStack application server is missing: ${target.args[0]}`)
    }

    const child = this.spawn(target.command, target.args, {
      cwd: target.cwd,
      env: {
        ...process.env,
        ...this.options.env,
        NODE_ENV: "production",
        HOST,
        PORT: String(port),
        OPEN_SWE_LOCAL_GRAPH_ORIGIN: graph.origin,
        OPEN_SWE_LOCAL_GRAPH_TOKEN: graph.token,
        ...(this.backendUrl
          ? { DASHBOARD_API_URL: this.backendUrl }
          : {}),
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    })
    this.child = child
    const append = (chunk: Buffer | string): void => {
      this.logs = `${this.logs}${chunk.toString()}`.slice(-16_000)
      if (process.env.OPEN_SWE_LOCAL_SERVER_LOGS === "1") {
        process.stderr.write(`[tanstack] ${chunk.toString()}`)
      }
    }
    child.stdout?.on("data", append)
    child.stderr?.on("data", append)

    let startupError: Error | null = null
    const exited = new Promise<void>((resolve) => {
      const markExited = (error: Error): void => {
        startupError ||= error
        if (this.child === child) {
          this.child = null
          this.ready = null
          this.appUrl = null
        }
        resolve()
      }
      child.once("error", (error) => {
        markExited(error)
      })
      child.once("exit", (code, signal) => {
        const reason = signal ? `signal ${signal}` : `exit code ${code}`
        markExited(new Error(`TanStack application server stopped with ${reason}`))
      })
    })
    const deadline = Date.now() + (this.options.startTimeoutMs || START_TIMEOUT_MS)
    while (Date.now() < deadline) {
      if (startupError) break
      try {
        const response = await this.fetch(`${this.appUrl}/health`, {
          signal: AbortSignal.timeout(1_000),
        })
        if (response.ok) return { appUrl: this.appUrl }
      } catch {}
      await Promise.race([delay(150), exited])
    }
    await this.close()
    const detail = this.logs.trim()
    if (startupError) {
      const error = startupError as Error
      throw new Error(`${error.message}${detail ? `\n${detail}` : ""}`)
    }
    throw new Error(`TanStack application server did not become healthy${detail ? `\n${detail}` : ""}`)
  }

  isTrustedUrl(value: string): boolean {
    if (!this.appUrl) return false
    try {
      return new URL(value).origin === this.appUrl
    } catch {
      return false
    }
  }

  origin(): string | null {
    return this.appUrl
  }

  async close(): Promise<void> {
    if (this.closing) return
    this.closing = true
    const child = this.child
    this.child = null
    this.ready = null
    this.appUrl = null
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
