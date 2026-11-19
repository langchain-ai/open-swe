import { spawn } from "node:child_process"

import { errorCode } from "./json"

export const DEFAULT_TIMEOUT_SECONDS = 300
export const OUTPUT_LIMIT_BYTES = 1024 * 1024
export const OUTPUT_KEEP_BYTES = 512 * 1024
const KILL_GRACE_MS = 2_000
const TIMEOUT_EXIT_CODE = 124

/** Keys whose values never reach the agent's shell, by suffix. */
const SECRET_SUFFIX = /(_API_KEY|_TOKEN|_SECRET|_SESSION|PASSWORD)$/i
/** …except the ones `gh` and `git` need to talk to GitHub. */
const KEPT_SECRETS = new Set(["GITHUB_TOKEN", "GH_TOKEN"])

export type ExecuteResult = {
  output: string
  exit_code: number | null
  truncated: boolean
}

export function commandEnvironment(
  source: Record<string, string | undefined> = process.env
): Record<string, string> {
  const env: Record<string, string> = {}
  for (const [key, value] of Object.entries(source)) {
    if (value === undefined) continue
    if (SECRET_SUFFIX.test(key) && !KEPT_SECRETS.has(key.toUpperCase()))
      continue
    env[key] = value
  }
  env["GIT_TERMINAL_PROMPT"] = "0"
  env["CI"] = "1"
  env["PAGER"] = "cat"
  env["GIT_PAGER"] = "cat"
  return env
}

export class OutputWindow {
  private readonly head: Uint8Array[] = []
  private headBytes = 0
  private readonly tail: Uint8Array[] = []
  private tailBytes = 0
  private total = 0

  push(chunk: Uint8Array): void {
    this.total += chunk.byteLength
    let rest = chunk
    if (this.headBytes < OUTPUT_KEEP_BYTES) {
      const take = Math.min(OUTPUT_KEEP_BYTES - this.headBytes, rest.byteLength)
      this.head.push(rest.subarray(0, take))
      this.headBytes += take
      rest = rest.subarray(take)
    }
    if (rest.byteLength === 0) return
    this.tail.push(rest)
    this.tailBytes += rest.byteLength
    for (
      let first = this.tail[0];
      first !== undefined &&
      this.tailBytes - first.byteLength >= OUTPUT_KEEP_BYTES;
      first = this.tail[0]
    ) {
      this.tail.shift()
      this.tailBytes -= first.byteLength
    }
  }

  finish(): { output: string; truncated: boolean } {
    const decoder = new TextDecoder()
    const head = concatChunks(this.head)
    const tail = concatChunks(this.tail)
    if (this.total <= OUTPUT_LIMIT_BYTES) {
      return {
        output: decoder.decode(concatChunks([head, tail])),
        truncated: false,
      }
    }
    const omitted = this.total - OUTPUT_KEEP_BYTES * 2
    return {
      output: `${decoder.decode(head)}\n[omitted ${omitted} bytes of output]\n${decoder.decode(tail.subarray(tail.byteLength - OUTPUT_KEEP_BYTES))}`,
      truncated: true,
    }
  }
}

function concatChunks(chunks: readonly Uint8Array[]): Uint8Array {
  let total = 0
  for (const chunk of chunks) total += chunk.byteLength
  const merged = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.byteLength
  }
  return merged
}

export interface LocalExecutorOptions {
  /**
   * The environment commands start from, before secrets are stripped. The
   * desktop app passes the user's login-shell environment: an app launched from
   * the Dock inherits neither their PATH nor anything their shell profile sets.
   */
  env?: Record<string, string | undefined>
}

/** Runs shell commands on this machine, in `cwd`, as the person running the app. */
export class LocalExecutor {
  private readonly env: Record<string, string>
  private readonly running = new Set<() => void>()

  constructor(
    private readonly cwd: string,
    options: LocalExecutorOptions = {}
  ) {
    this.env = commandEnvironment(options.env)
  }

  async execute(
    command: string,
    timeout: number | null
  ): Promise<ExecuteResult> {
    const seconds =
      timeout !== null && timeout > 0 ? timeout : DEFAULT_TIMEOUT_SECONDS
    // Detached puts the shell in its own process group, so a timeout can signal
    // everything it spawned; killing only `sh` leaves children holding the pipes.
    const proc = spawn("sh", ["-c", command], {
      cwd: this.cwd,
      stdio: ["ignore", "pipe", "pipe"],
      env: this.env,
      detached: true,
    })
    const signalGroup = (signal: NodeJS.Signals): void => {
      if (proc.pid === undefined) return
      try {
        process.kill(-proc.pid, signal)
      } catch (cause) {
        if (errorCode(cause) !== "ESRCH") throw cause
      }
    }

    const captured = new OutputWindow()
    let timedOut = false
    let stopped = false
    let escalation: ReturnType<typeof setTimeout> | null = null
    const terminate = () => {
      signalGroup("SIGTERM")
      escalation ??= setTimeout(() => signalGroup("SIGKILL"), KILL_GRACE_MS)
    }
    const stop = () => {
      stopped = true
      terminate()
    }
    this.running.add(stop)
    const deadline = setTimeout(() => {
      timedOut = true
      terminate()
    }, seconds * 1000)
    proc.stdout.on("data", (chunk: Buffer) => captured.push(chunk))
    proc.stderr.on("data", (chunk: Buffer) => captured.push(chunk))

    let exitCode: number | null = null
    try {
      exitCode = await new Promise<number | null>((done, fail) => {
        proc.once("error", fail)
        proc.once("close", (code) => done(code))
      })
    } finally {
      this.running.delete(stop)
      clearTimeout(deadline)
      if (escalation !== null) clearTimeout(escalation)
    }

    const { output, truncated } = captured.finish()
    if (stopped) {
      return {
        output: `${output}\n[command stopped]`,
        exit_code: TIMEOUT_EXIT_CODE,
        truncated,
      }
    }
    if (timedOut) {
      return {
        output: `${output}\n[command timed out after ${seconds}s]`,
        exit_code: TIMEOUT_EXIT_CODE,
        truncated,
      }
    }
    return { output, exit_code: exitCode, truncated }
  }

  /** Terminate every running command; each resolves once its process group exits. */
  stopAll(): void {
    for (const stop of this.running) stop()
  }
}
