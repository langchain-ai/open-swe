import { spawn } from "node:child_process"
import { createReadStream } from "node:fs"
import { mkdtemp, open, rm, stat } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

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
    // Output goes to a file, not a pipe: a server the command starts
    // (`postfix start`, `qemu … &`) inherits it, and a pipe would keep the
    // command from ever finishing, then break the server's writes once this
    // process exits. The file outlives both.
    const outputDir = await mkdtemp(join(tmpdir(), "open-swe-command-"))
    try {
      const outputPath = join(outputDir, "output")
      const file = await open(outputPath, "w", 0o600)
      let proc: ReturnType<typeof spawn>
      let exited: Promise<number | null>
      try {
        // Detached puts the shell in its own process group, so a timeout can
        // signal everything it spawned.
        proc = spawn("sh", ["-c", command], {
          cwd: this.cwd,
          stdio: ["ignore", file.fd, file.fd],
          env: this.env,
          detached: true,
        })
        // Listen before awaiting anything: a fast command exits, and a missing
        // working directory fails the spawn, while the file is still closing.
        const child = proc
        exited = new Promise<number | null>((done, fail) => {
          child.once("error", fail)
          child.once("exit", (code) => done(code))
        })
        exited.catch(() => {})
      } finally {
        await file.close()
      }
      const group = proc.pid
      const signalGroup = (signal: NodeJS.Signals | 0): boolean => {
        if (group === undefined) return false
        try {
          process.kill(-group, signal)
          return true
        } catch (cause) {
          if (errorCode(cause) !== "ESRCH") throw cause
          return false
        }
      }

      let timedOut = false
      let stopped = false
      let escalation: ReturnType<typeof setTimeout> | null = null
      let markKilled = (): void => {}
      const killed = new Promise<void>((done) => {
        markKilled = done
      })
      const terminate = () => {
        signalGroup("SIGTERM")
        escalation ??= setTimeout(() => {
          signalGroup("SIGKILL")
          markKilled()
        }, KILL_GRACE_MS)
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

      let exitCode: number | null = null
      try {
        exitCode = await exited
        // A terminated command is over only once nothing in its group survives
        // SIGTERM; what a finished command left running keeps running.
        if (escalation !== null && signalGroup(0)) await killed
      } finally {
        this.running.delete(stop)
        clearTimeout(deadline)
        if (escalation !== null) clearTimeout(escalation)
      }

      const captured = new OutputWindow()
      // Up to the size at exit: a server the command left running may still be writing.
      const { size } = await stat(outputPath)
      if (size > 0) {
        for await (const chunk of createReadStream(outputPath, {
          end: size - 1,
        })) {
          captured.push(chunk as Buffer)
        }
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
    } finally {
      await rm(outputDir, { recursive: true, force: true })
    }
  }

  /** Terminate every running command; each resolves once its process group exits. */
  stopAll(): void {
    for (const stop of this.running) stop()
  }
}
