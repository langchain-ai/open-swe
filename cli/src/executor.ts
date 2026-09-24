import { spawn } from "node:child_process"
import { mkdir, readFile, writeFile } from "node:fs/promises"
import { dirname, isAbsolute, normalize, resolve } from "node:path"

import {
  errorCode,
  errorMessage,
  isRecord,
  numberAt,
  stringArrayAt,
  stringAt,
  type JsonObject,
} from "./json.ts"

export const DEFAULT_TIMEOUT_SECONDS = 300
export const OUTPUT_LIMIT_BYTES = 1024 * 1024
export const OUTPUT_KEEP_BYTES = 512 * 1024
const KILL_GRACE_MS = 2_000
const TIMEOUT_EXIT_CODE = 124

/** Keys whose values never reach the agent's shell, by suffix. */
const SECRET_SUFFIX = /(_API_KEY|_TOKEN|_SECRET|PASSWORD)$/i
/** …except the ones `gh` and `git` need to talk to GitHub. */
const KEPT_SECRETS = new Set(["GITHUB_TOKEN", "GH_TOKEN"])

const BASE64 = /^[A-Za-z0-9+/\-_]*={0,2}$/

export type ExecuteResult = {
  output: string
  exit_code: number | null
  truncated: boolean
}

export type UploadResponse = {
  path: string
  error: string | null
}

export type DownloadResponse = {
  path: string
  content_base64: string | null
  error: string | null
}

export type DispatchOutcome = { result: JsonObject } | { error: string }

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

export function truncateOutput(bytes: Uint8Array): {
  output: string
  truncated: boolean
} {
  const decoder = new TextDecoder()
  if (bytes.byteLength <= OUTPUT_LIMIT_BYTES) {
    return { output: decoder.decode(bytes), truncated: false }
  }
  const omitted = bytes.byteLength - OUTPUT_KEEP_BYTES * 2
  const head = decoder.decode(bytes.subarray(0, OUTPUT_KEEP_BYTES))
  const tail = decoder.decode(
    bytes.subarray(bytes.byteLength - OUTPUT_KEEP_BYTES)
  )
  return {
    output: `${head}\n[oswe: omitted ${omitted} bytes of output]\n${tail}`,
    truncated: true,
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

function decodeBase64(value: string): Uint8Array {
  const compact = value.replace(/\s+/g, "")
  if (!BASE64.test(compact) || compact.length % 4 === 1) {
    throw new Error("invalid base64 content")
  }
  return new Uint8Array(Buffer.from(compact, "base64"))
}

function fileError(cause: unknown, missing: string): string {
  switch (errorCode(cause)) {
    case "EACCES":
    case "EPERM":
      return "permission_denied"
    case "ENOENT":
      return missing
    case "EISDIR":
      return "is_a_directory"
    case "ENOTDIR":
    case "ENAMETOOLONG":
    case "EINVAL":
      return "invalid_path"
    default:
      return errorMessage(cause)
  }
}

/**
 * Runs the remote agent's sandbox requests against the local checkout. Paths
 * are unconfined, matching the desktop app's local mode.
 */
export class LocalExecutor {
  constructor(private readonly root: string) {}

  async dispatch(
    method: string,
    params: Record<string, unknown>
  ): Promise<DispatchOutcome> {
    switch (method) {
      case "execute": {
        const command = stringAt(params, "command")
        if (command === null)
          return { error: "execute requires a command string" }
        const result = await this.execute(command, numberAt(params, "timeout"))
        return { result: { ...result } }
      }
      case "upload_files": {
        const files = uploadEntries(params)
        if (files === null) {
          return {
            error: "upload_files requires files with path and content_base64",
          }
        }
        return { result: { responses: await this.uploadFiles(files) } }
      }
      case "download_files": {
        const paths = stringArrayAt(params, "paths")
        if (paths === null)
          return { error: "download_files requires a paths array" }
        return { result: { responses: await this.downloadFiles(paths) } }
      }
      default:
        return { error: `unsupported method ${method}` }
    }
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
      cwd: this.root,
      stdio: ["ignore", "pipe", "pipe"],
      env: commandEnvironment(),
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

    const chunks: Uint8Array[] = []
    let timedOut = false
    let escalation: ReturnType<typeof setTimeout> | null = null
    const deadline = setTimeout(() => {
      timedOut = true
      signalGroup("SIGTERM")
      escalation = setTimeout(() => signalGroup("SIGKILL"), KILL_GRACE_MS)
    }, seconds * 1000)
    proc.stdout.on("data", (chunk: Buffer) => chunks.push(chunk))
    proc.stderr.on("data", (chunk: Buffer) => chunks.push(chunk))

    let exitCode: number | null = null
    try {
      exitCode = await new Promise<number | null>((done, fail) => {
        proc.once("error", fail)
        proc.once("close", (code) => done(code))
      })
    } finally {
      clearTimeout(deadline)
      if (escalation !== null) clearTimeout(escalation)
    }

    const { output, truncated } = truncateOutput(concatChunks(chunks))
    if (timedOut) {
      return {
        output: `${output}\n[oswe: command timed out after ${seconds}s]`,
        exit_code: TIMEOUT_EXIT_CODE,
        truncated,
      }
    }
    return { output, exit_code: exitCode, truncated }
  }

  async uploadFiles(
    files: readonly { path: string; content_base64: string }[]
  ): Promise<UploadResponse[]> {
    const responses: UploadResponse[] = []
    for (const file of files) {
      const target = this.target(file.path)
      if (target === null) {
        responses.push({ path: file.path, error: "invalid_path" })
        continue
      }
      try {
        const bytes = decodeBase64(file.content_base64)
        await mkdir(dirname(target), { recursive: true })
        await writeFile(target, bytes)
        responses.push({ path: file.path, error: null })
      } catch (cause) {
        responses.push({
          path: file.path,
          error: fileError(cause, "invalid_path"),
        })
      }
    }
    return responses
  }

  async downloadFiles(paths: readonly string[]): Promise<DownloadResponse[]> {
    const responses: DownloadResponse[] = []
    for (const path of paths) {
      const target = this.target(path)
      if (target === null) {
        responses.push({ path, content_base64: null, error: "invalid_path" })
        continue
      }
      try {
        const bytes = await readFile(target)
        responses.push({
          path,
          content_base64: bytes.toString("base64"),
          error: null,
        })
      } catch (cause) {
        responses.push({
          path,
          content_base64: null,
          error: fileError(cause, "file_not_found"),
        })
      }
    }
    return responses
  }

  private target(path: string): string | null {
    if (!path || path.includes("\0")) return null
    return isAbsolute(path) ? normalize(path) : resolve(this.root, path)
  }
}

function uploadEntries(
  params: Record<string, unknown>
): { path: string; content_base64: string }[] | null {
  const files = params["files"]
  if (!Array.isArray(files)) return null
  const entries: { path: string; content_base64: string }[] = []
  for (const entry of files) {
    if (!isRecord(entry)) return null
    const path = stringAt(entry, "path")
    const content = stringAt(entry, "content_base64")
    if (path === null || content === null) return null
    entries.push({ path, content_base64: content })
  }
  return entries
}
