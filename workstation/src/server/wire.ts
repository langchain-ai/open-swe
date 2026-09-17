import type {
  DeleteResult,
  EditResult,
  ExecuteEvent,
  ExecuteOptions,
  FileData,
  FileDownloadResponse,
  FileInfo,
  FileUploadResponse,
  GlobResult,
  GrepMatch,
  GrepOptions,
  GrepResult,
  LsResult,
  ReadOptions,
  ReadResult,
  UploadFile,
  WriteResult,
} from "../backend/types.ts"

/**
 * The single place where the camelCase TypeScript results of
 * `backend/types.ts` meet the snake_case JSON the Python client feeds straight
 * into the `deepagents.backends.protocol` dataclasses. Field names here are the
 * dataclass field names; optional fields are omitted rather than null-filled so
 * the dataclass defaults apply.
 */

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type JsonObject = { [key: string]: JsonValue }

export class WireError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "WireError"
  }
}

const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/

class Fields {
  private readonly source: Readonly<Record<string, unknown>>

  constructor(value: unknown, allowed: readonly string[]) {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new WireError("body must be a JSON object")
    }
    const source = value as Readonly<Record<string, unknown>>
    for (const key of Object.keys(source)) {
      if (!allowed.includes(key)) {
        throw new WireError(`unknown field "${key}"`)
      }
    }
    this.source = source
  }

  requiredString(key: string): string {
    const value = this.source[key]
    if (typeof value !== "string" || value.length === 0) {
      throw new WireError(`field "${key}" must be a non-empty string`)
    }
    return value
  }

  optionalString(key: string): string | undefined {
    const value = this.source[key]
    if (value === undefined || value === null) return undefined
    if (typeof value !== "string") {
      throw new WireError(`field "${key}" must be a string`)
    }
    return value
  }

  requiredText(key: string): string {
    const value = this.source[key]
    if (typeof value !== "string") {
      throw new WireError(`field "${key}" must be a string`)
    }
    return value
  }

  optionalInteger(key: string): number | undefined {
    const value = this.source[key]
    if (value === undefined || value === null) return undefined
    if (typeof value !== "number" || !Number.isSafeInteger(value)) {
      throw new WireError(`field "${key}" must be an integer`)
    }
    return value
  }

  optionalBoolean(key: string): boolean | undefined {
    const value = this.source[key]
    if (value === undefined || value === null) return undefined
    if (typeof value !== "boolean") {
      throw new WireError(`field "${key}" must be a boolean`)
    }
    return value
  }

  requiredStringArray(key: string): string[] {
    const value = this.source[key]
    if (!Array.isArray(value)) {
      throw new WireError(`field "${key}" must be an array of strings`)
    }
    return value.map((item: unknown) => {
      if (typeof item !== "string" || item.length === 0) {
        throw new WireError(`field "${key}" must be an array of strings`)
      }
      return item
    })
  }

  requiredObjectArray(key: string, allowed: readonly string[]): Fields[] {
    const value = this.source[key]
    if (!Array.isArray(value)) {
      throw new WireError(`field "${key}" must be an array of objects`)
    }
    return value.map((item: unknown) => new Fields(item, allowed))
  }

  requiredBase64(key: string): Uint8Array {
    const value = this.requiredText(key)
    if (value.length % 4 !== 0 || !BASE64.test(value)) {
      throw new WireError(`field "${key}" must be base64`)
    }
    return new Uint8Array(Buffer.from(value, "base64"))
  }
}

export interface RootRequest {
  readonly root: string
}

export interface LsRequest extends RootRequest {
  readonly path: string
}

export interface ReadRequest extends RootRequest {
  readonly filePath: string
  readonly options: ReadOptions
}

export interface WriteRequest extends RootRequest {
  readonly filePath: string
  readonly content: string
}

export interface EditRequest extends RootRequest {
  readonly filePath: string
  readonly oldString: string
  readonly newString: string
  readonly replaceAll: boolean
}

export interface DeleteRequest extends RootRequest {
  readonly filePath: string
}

export interface GrepRequest extends RootRequest {
  readonly pattern: string
  readonly options: GrepOptions
}

export interface GlobRequest extends RootRequest {
  readonly pattern: string
  readonly path: string | undefined
}

export interface UploadRequest extends RootRequest {
  readonly files: readonly UploadFile[]
}

export interface DownloadRequest extends RootRequest {
  readonly paths: readonly string[]
}

export interface ExecuteRequest extends RootRequest {
  readonly command: string
  readonly options: ExecuteOptions
}

function defined<T>(key: string, value: T | undefined): Record<string, T> {
  return value === undefined ? {} : { [key]: value }
}

export function parseLsRequest(body: unknown): LsRequest {
  const fields = new Fields(body, ["root", "path"])
  return {
    root: fields.requiredString("root"),
    path: fields.requiredString("path"),
  }
}

export function parseReadRequest(body: unknown): ReadRequest {
  const fields = new Fields(body, ["root", "file_path", "offset", "limit"])
  return {
    root: fields.requiredString("root"),
    filePath: fields.requiredString("file_path"),
    options: {
      ...defined("offset", fields.optionalInteger("offset")),
      ...defined("limit", fields.optionalInteger("limit")),
    },
  }
}

export function parseWriteRequest(body: unknown): WriteRequest {
  const fields = new Fields(body, ["root", "file_path", "content"])
  return {
    root: fields.requiredString("root"),
    filePath: fields.requiredString("file_path"),
    content: fields.requiredText("content"),
  }
}

export function parseEditRequest(body: unknown): EditRequest {
  const fields = new Fields(body, [
    "root",
    "file_path",
    "old_string",
    "new_string",
    "replace_all",
  ])
  return {
    root: fields.requiredString("root"),
    filePath: fields.requiredString("file_path"),
    oldString: fields.requiredText("old_string"),
    newString: fields.requiredText("new_string"),
    replaceAll: fields.optionalBoolean("replace_all") ?? false,
  }
}

export function parseDeleteRequest(body: unknown): DeleteRequest {
  const fields = new Fields(body, ["root", "file_path"])
  return {
    root: fields.requiredString("root"),
    filePath: fields.requiredString("file_path"),
  }
}

export function parseGrepRequest(body: unknown): GrepRequest {
  const fields = new Fields(body, [
    "root",
    "pattern",
    "path",
    "glob",
    "max_count",
    "context_lines",
  ])
  return {
    root: fields.requiredString("root"),
    pattern: fields.requiredText("pattern"),
    options: {
      ...defined("path", fields.optionalString("path")),
      ...defined("glob", fields.optionalString("glob")),
      ...defined("maxCount", fields.optionalInteger("max_count")),
      ...defined("contextLines", fields.optionalInteger("context_lines")),
    },
  }
}

export function parseGlobRequest(body: unknown): GlobRequest {
  const fields = new Fields(body, ["root", "pattern", "path"])
  return {
    root: fields.requiredString("root"),
    pattern: fields.requiredText("pattern"),
    path: fields.optionalString("path"),
  }
}

export function parseUploadRequest(body: unknown): UploadRequest {
  const fields = new Fields(body, ["root", "files"])
  const root = fields.requiredString("root")
  const files = fields
    .requiredObjectArray("files", ["path", "content_base64"])
    .map((file) => ({
      path: file.requiredString("path"),
      content: file.requiredBase64("content_base64"),
    }))
  return { root, files }
}

export function parseDownloadRequest(body: unknown): DownloadRequest {
  const fields = new Fields(body, ["root", "paths"])
  return {
    root: fields.requiredString("root"),
    paths: fields.requiredStringArray("paths"),
  }
}

export function parseExecuteRequest(body: unknown): ExecuteRequest {
  const fields = new Fields(body, [
    "root",
    "command",
    "cwd",
    "timeout_seconds",
    "max_output_bytes",
  ])
  return {
    root: fields.requiredString("root"),
    command: fields.requiredText("command"),
    options: {
      ...defined("cwd", fields.optionalString("cwd")),
      ...defined("timeoutSeconds", fields.optionalInteger("timeout_seconds")),
      ...defined("maxOutputBytes", fields.optionalInteger("max_output_bytes")),
    },
  }
}

function encodeFileData(fileData: FileData): JsonObject {
  return {
    content: fileData.content,
    encoding: fileData.encoding,
    ...defined("created_at", fileData.createdAt),
    ...defined("modified_at", fileData.modifiedAt),
  }
}

function encodeFileInfo(info: FileInfo): JsonObject {
  return {
    path: info.path,
    ...defined("is_dir", info.isDir),
    ...defined("size", info.size),
    ...defined("modified_at", info.modifiedAt),
  }
}

function encodeGrepMatch(match: GrepMatch): JsonObject {
  return {
    path: match.path,
    line: match.line,
    text: match.text,
    ...defined(
      "context_before",
      match.contextBefore?.map((line) => ({ line: line.line, text: line.text }))
    ),
    ...defined(
      "context_after",
      match.contextAfter?.map((line) => ({ line: line.line, text: line.text }))
    ),
  }
}

/**
 * `ReadResult.__post_init__` validates the pagination fields as a group, so a
 * combination it would reject is narrowed to the largest valid subset here
 * instead of raising inside the agent. Dropping `next_offset` loses a resume
 * hint; emitting an inconsistent one would silently skip unread lines.
 */
export function encodeReadResult(result: ReadResult): JsonObject {
  if (result.noLinesRequested === true) {
    return {
      no_lines_requested: true,
      ...defined(
        "file_data",
        result.fileData === undefined
          ? undefined
          : encodeFileData(result.fileData)
      ),
    }
  }

  const hasWindow =
    result.startLine !== undefined &&
    result.endLine !== undefined &&
    result.startLine >= 1 &&
    result.endLine >= result.startLine
  const endLine = hasWindow ? result.endLine : undefined
  const totalLines =
    endLine !== undefined &&
    result.totalLines !== undefined &&
    result.totalLines >= endLine
      ? result.totalLines
      : undefined
  const nextOffset =
    endLine !== undefined && result.nextOffset === endLine
      ? result.nextOffset
      : undefined

  return {
    ...defined("error", result.error),
    ...defined(
      "file_data",
      result.fileData === undefined
        ? undefined
        : encodeFileData(result.fileData)
    ),
    ...defined("total_lines", totalLines),
    ...defined("start_line", hasWindow ? result.startLine : undefined),
    ...defined("end_line", endLine),
    ...defined("next_offset", nextOffset),
  }
}

export function encodePathResult(
  result: WriteResult | DeleteResult
): JsonObject {
  return {
    ...defined("error", result.error),
    ...defined("path", result.path),
  }
}

export function encodeEditResult(result: EditResult): JsonObject {
  return {
    ...defined("error", result.error),
    ...defined("path", result.path),
    ...defined("occurrences", result.occurrences),
  }
}

export function encodeLsResult(result: LsResult): JsonObject {
  return {
    ...defined("error", result.error),
    ...defined("entries", result.entries?.map(encodeFileInfo)),
  }
}

export function encodeGrepResult(result: GrepResult): JsonObject {
  return {
    ...defined("error", result.error),
    ...defined("matches", result.matches?.map(encodeGrepMatch)),
    truncated: result.truncated,
  }
}

export function encodeGlobResult(result: GlobResult): JsonObject {
  return {
    ...defined("error", result.error),
    ...defined("matches", result.matches?.map(encodeFileInfo)),
    truncated: result.truncated,
    ...defined("truncation_reason", result.truncationReason),
  }
}

export function encodeUploadResponses(
  responses: readonly FileUploadResponse[]
): JsonObject {
  return {
    files: responses.map((response) => ({
      path: response.path,
      ...defined("error", response.error),
    })),
  }
}

export function encodeDownloadResponses(
  responses: readonly FileDownloadResponse[]
): JsonObject {
  return {
    files: responses.map((response) => ({
      path: response.path,
      ...defined(
        "content_base64",
        response.content === undefined
          ? undefined
          : Buffer.from(response.content).toString("base64")
      ),
      ...defined("error", response.error),
    })),
  }
}

export function encodeExecuteEvent(event: ExecuteEvent): JsonObject {
  if (event.type === "output") {
    return { type: "output", data: event.data }
  }
  return {
    type: "exit",
    exit_code: event.exitCode,
    truncated: event.truncated,
  }
}
