/**
 * TypeScript mirror of the deep agents backend protocol
 * (`deepagents.backends.protocol`). Field semantics must match the Python
 * dataclasses exactly, because `src/server/wire.ts` serializes these to the
 * snake_case payloads the Python `WorkstationBackend` feeds straight into
 * those dataclasses.
 */

export type FileOperationError =
  | "file_not_found"
  | "permission_denied"
  | "is_directory"
  | "invalid_path"

export interface FileInfo {
  readonly path: string
  readonly isDir?: boolean
  readonly size?: number
  readonly modifiedAt?: string
}

export interface FileData {
  readonly content: string
  readonly encoding: "utf-8" | "base64"
  readonly createdAt?: string
  readonly modifiedAt?: string
}

export interface ContextLine {
  readonly line: number
  readonly text: string
}

export interface GrepMatch {
  readonly path: string
  readonly line: number
  readonly text: string
  readonly contextBefore?: readonly ContextLine[]
  readonly contextAfter?: readonly ContextLine[]
}

/**
 * The pagination fields are not independent: `startLine`/`endLine` are a pair,
 * `nextOffset` must equal `endLine`, and `totalLines >= endLine`. The Python
 * dataclass rejects malformed combinations in `__post_init__`, so producing one
 * here fails at the boundary rather than in the agent.
 */
export interface ReadResult {
  readonly error?: string
  readonly fileData?: FileData
  readonly totalLines?: number
  readonly startLine?: number
  readonly endLine?: number
  readonly nextOffset?: number
  readonly noLinesRequested?: boolean
}

export interface WriteResult {
  readonly error?: string
  readonly path?: string
}

export interface EditResult {
  readonly error?: string
  readonly path?: string
  readonly occurrences?: number
}

export interface DeleteResult {
  readonly error?: string
  readonly path?: string
}

export interface LsResult {
  readonly error?: string
  readonly entries?: readonly FileInfo[]
}

export interface GrepResult {
  readonly error?: string
  readonly matches?: readonly GrepMatch[]
  readonly truncated: boolean
}

export type GlobTruncationReason = "budget" | "unreadable" | "transport"

export interface GlobResult {
  readonly error?: string
  readonly matches?: readonly FileInfo[]
  readonly truncated: boolean
  readonly truncationReason?: GlobTruncationReason
}

export interface FileUploadResponse {
  readonly path: string
  readonly error?: FileOperationError | string
}

export interface FileDownloadResponse {
  readonly path: string
  readonly content?: Uint8Array
  readonly error?: FileOperationError | string
}

export interface UploadFile {
  readonly path: string
  readonly content: Uint8Array
}

export interface ExecuteResponse {
  readonly output: string
  readonly exitCode: number | null
  readonly truncated: boolean
}

export type ExecuteEvent =
  | { readonly type: "output"; readonly data: string }
  | {
      readonly type: "exit"
      readonly exitCode: number | null
      readonly truncated: boolean
    }

export interface ExecuteOptions {
  readonly cwd?: string
  readonly timeoutSeconds?: number
  readonly env?: Readonly<Record<string, string>>
  readonly maxOutputBytes?: number
}

export interface GrepOptions {
  readonly path?: string
  readonly glob?: string
  readonly maxCount?: number
  readonly contextLines?: number
}

export interface ReadOptions {
  readonly offset?: number
  readonly limit?: number
}

/**
 * The backend reaches the whole filesystem as the user does. `defaultDir` is
 * only where a relative path resolves from, an omitted `execute` cwd runs, and
 * an omitted search path starts; it is not a boundary.
 *
 * A failure is reported as a result `error`, never by throwing: an unreadable
 * path is an ordinary tool failure the agent should read and recover from.
 */
export interface Backend {
  readonly defaultDir: string
  ls(path: string): Promise<LsResult>
  read(filePath: string, options?: ReadOptions): Promise<ReadResult>
  write(filePath: string, content: string): Promise<WriteResult>
  edit(
    filePath: string,
    oldString: string,
    newString: string,
    replaceAll?: boolean
  ): Promise<EditResult>
  delete(filePath: string): Promise<DeleteResult>
  grep(pattern: string, options?: GrepOptions): Promise<GrepResult>
  glob(pattern: string, path?: string): Promise<GlobResult>
  uploadFiles(files: readonly UploadFile[]): Promise<FileUploadResponse[]>
  downloadFiles(paths: readonly string[]): Promise<FileDownloadResponse[]>
  execute(command: string, options?: ExecuteOptions): Promise<ExecuteResponse>
  executeStream(
    command: string,
    options?: ExecuteOptions
  ): AsyncIterable<ExecuteEvent>
}
