import type { ChildProcess } from "node:child_process"
import { spawn } from "node:child_process"
import { StringDecoder } from "node:string_decoder"

import { errorMessage, resolveWithinRoot } from "./paths.ts"
import type { ExecuteEvent, ExecuteOptions, ExecuteResponse } from "./types.ts"

export interface ExecuteConfig {
  readonly shell?: string
  readonly defaultTimeoutSeconds?: number
  readonly maxOutputBytes?: number
  readonly env?: Readonly<Record<string, string>>
}

/**
 * The only variables a command inherits from this process. The server's own
 * environment holds provider credentials and API keys, and an agent shell
 * command is exactly the place they must not appear.
 */
const SHELL_ENV_KEYS = [
  "HOME",
  "LANG",
  "LC_ALL",
  "PATH",
  "SHELL",
  "TMPDIR",
] as const

const FALLBACK_SHELL = "/bin/bash"
const DEFAULT_TIMEOUT_SECONDS = 300
const DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
const TIMEOUT_EXIT_CODE = 124
const KILL_GRACE_MS = 1_000
const FAILED_EXIT_CODE = 1

function shellEnv(
  config: ExecuteConfig | undefined,
  options: ExecuteOptions | undefined
): Record<string, string> {
  const base: Record<string, string> = {}
  for (const key of SHELL_ENV_KEYS) {
    const value = process.env[key]
    if (value !== undefined && value !== "") base[key] = value
  }
  return { ...base, ...config?.env, ...options?.env }
}

/**
 * Signal the command's whole process group, not just the shell: a killed shell
 * leaves its children running, still holding the pipes and the repository.
 */
function killGroup(child: ChildProcess, signal: NodeJS.Signals): string | null {
  const pid = child.pid
  if (
    pid === undefined ||
    child.exitCode !== null ||
    child.signalCode !== null
  ) {
    return null
  }
  try {
    process.kill(-pid, signal)
    return null
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code
    return code === "ESRCH" ? null : errorMessage(error)
  }
}

export async function* executeStream(
  rootDir: string,
  command: string,
  options?: ExecuteOptions,
  config?: ExecuteConfig
): AsyncIterable<ExecuteEvent> {
  if (command.length === 0) {
    yield { type: "output", data: "Error: command must be a non-empty string" }
    yield { type: "exit", exitCode: FAILED_EXIT_CODE, truncated: false }
    return
  }

  let cwd: string
  try {
    cwd = await resolveWithinRoot(rootDir, options?.cwd ?? rootDir)
  } catch (error) {
    yield { type: "output", data: errorMessage(error) }
    yield { type: "exit", exitCode: FAILED_EXIT_CODE, truncated: false }
    return
  }

  const env = shellEnv(config, options)
  const shell = config?.shell ?? env["SHELL"] ?? FALLBACK_SHELL
  const maxOutputBytes =
    options?.maxOutputBytes ??
    config?.maxOutputBytes ??
    DEFAULT_MAX_OUTPUT_BYTES
  const timeoutSeconds =
    options?.timeoutSeconds ??
    config?.defaultTimeoutSeconds ??
    DEFAULT_TIMEOUT_SECONDS

  // A zero or NaN bound would otherwise reach `setTimeout` and kill every
  // command the instant it starts, or truncate before the first byte.
  const invalid =
    !Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0
      ? `timeout must be positive, got ${timeoutSeconds}`
      : !Number.isFinite(maxOutputBytes) || maxOutputBytes <= 0
        ? `maxOutputBytes must be positive, got ${maxOutputBytes}`
        : null
  if (invalid !== null) {
    yield { type: "output", data: `Error: ${invalid}` }
    yield { type: "exit", exitCode: FAILED_EXIT_CODE, truncated: false }
    return
  }

  const queue: ExecuteEvent[] = []
  let wake: (() => void) | null = null
  let finished = false
  let captured = 0
  let truncated = false
  let timedOut = false
  let settled = false

  const push = (event: ExecuteEvent): void => {
    queue.push(event)
    const resume = wake
    wake = null
    resume?.()
  }

  const emit = (data: string): void => {
    if (data.length > 0) push({ type: "output", data })
  }

  /**
   * Slicing at the byte budget can cut a multi-byte sequence in half, so the
   * decoder holds the partial tail until the rest of it arrives; past the
   * budget the tail is dropped rather than emitted broken.
   */
  const capture = (decoder: StringDecoder, chunk: Buffer): void => {
    if (truncated) return
    const room = maxOutputBytes - captured
    const slice = chunk.length <= room ? chunk : chunk.subarray(0, room)
    captured += slice.length
    emit(decoder.write(slice))
    if (chunk.length > room) {
      truncated = true
      emit(`\n\n... Output truncated at ${maxOutputBytes} bytes.`)
    }
  }

  const child = spawn(shell, ["-c", command], {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    detached: true,
  })

  const settle = (exitCode: number | null): void => {
    if (settled) return
    settled = true
    if (timedOut) {
      emit(
        `${captured > 0 ? "\n" : ""}Error: command timed out after ` +
          `${timeoutSeconds} seconds`
      )
    }
    finished = true
    push({
      type: "exit",
      exitCode: timedOut ? TIMEOUT_EXIT_CODE : exitCode,
      truncated,
    })
  }

  for (const stream of [child.stdout, child.stderr]) {
    if (stream === null) continue
    const decoder = new StringDecoder("utf8")
    stream.on("data", (chunk: Buffer) => capture(decoder, chunk))
    stream.on("end", () => {
      if (!truncated) emit(decoder.end())
    })
    stream.on("error", (error: Error) => emit(errorMessage(error)))
  }

  child.on("error", (error) => {
    emit(errorMessage(error))
    settle(FAILED_EXIT_CODE)
  })
  child.on("close", (code) => settle(code))

  let graceTimer: NodeJS.Timeout | undefined
  const reportKill = (failure: string | null): void => {
    if (failure !== null) emit(failure)
  }
  const timer = setTimeout(() => {
    timedOut = true
    reportKill(killGroup(child, "SIGTERM"))
    graceTimer = setTimeout(
      () => reportKill(killGroup(child, "SIGKILL")),
      KILL_GRACE_MS
    )
  }, timeoutSeconds * 1_000)

  try {
    for (;;) {
      const next = queue.shift()
      if (next !== undefined) {
        yield next
        continue
      }
      if (finished) return
      await new Promise<void>((resolve) => {
        wake = resolve
      })
    }
  } finally {
    clearTimeout(timer)
    if (graceTimer !== undefined) clearTimeout(graceTimer)
    if (!settled) {
      const failure = killGroup(child, "SIGKILL")
      if (failure !== null) process.emitWarning(failure)
      child.stdout?.destroy()
      child.stderr?.destroy()
    }
  }
}

export async function execute(
  rootDir: string,
  command: string,
  options?: ExecuteOptions,
  config?: ExecuteConfig
): Promise<ExecuteResponse> {
  const chunks: string[] = []
  let exitCode: number | null = null
  let truncated = false
  for await (const event of executeStream(rootDir, command, options, config)) {
    if (event.type === "output") {
      chunks.push(event.data)
    } else {
      exitCode = event.exitCode
      truncated = event.truncated
    }
  }
  return { output: chunks.join(""), exitCode, truncated }
}
