import { constants as fsConstants, type Stats } from "node:fs"
import {
  lstat,
  mkdir,
  open,
  readdir,
  realpath,
  rm,
  stat,
  unlink,
} from "node:fs/promises"
import path from "node:path"

import { errorMessage, resolvePath, toFileOperationError } from "./paths.ts"
import type {
  DeleteResult,
  EditResult,
  FileDownloadResponse,
  FileInfo,
  FileUploadResponse,
  LsResult,
  ReadOptions,
  ReadResult,
  UploadFile,
  WriteResult,
} from "./types.ts"

const DEFAULT_READ_LIMIT = 2000

const EMPTY_CONTENT_WARNING =
  "System reminder: File exists but has empty contents"

const MAX_VIDEO_INPUT_BYTES = 1024 * 1024 * 1024

type FileType = "text" | "image" | "audio" | "video" | "file"

const EXTENSION_FILE_TYPES: Readonly<Record<string, FileType>> = {
  ".png": "image",
  ".jpeg": "image",
  ".jpg": "image",
  ".webp": "image",
  ".gif": "image",
  ".heic": "image",
  ".heif": "image",
  ".mp4": "video",
  ".mpeg": "video",
  ".mov": "video",
  ".avi": "video",
  ".flv": "video",
  ".mpg": "video",
  ".webm": "video",
  ".wmv": "video",
  ".3gpp": "video",
  ".mkv": "video",
  ".wav": "audio",
  ".mp3": "audio",
  ".aiff": "audio",
  ".aac": "audio",
  ".ogg": "audio",
  ".flac": "audio",
  ".pdf": "file",
  ".ppt": "file",
  ".pptx": "file",
}

const READ_FLAGS = fsConstants.O_RDONLY | fsConstants.O_NOFOLLOW

const CREATE_FLAGS =
  fsConstants.O_WRONLY |
  fsConstants.O_CREAT |
  fsConstants.O_TRUNC |
  fsConstants.O_NOFOLLOW

const TRUNCATE_FLAGS =
  fsConstants.O_WRONLY | fsConstants.O_TRUNC | fsConstants.O_NOFOLLOW

const FILE_MODE = 0o644

/** Codes `Path.is_file()` and `Path.is_dir()` answer `False` for. */
const IGNORED_STAT_CODES: ReadonlySet<string> = new Set([
  "ENOENT",
  "ENOTDIR",
  "EBADF",
  "ELOOP",
  "EINVAL",
])

/**
 * Every line terminator `str.splitlines` recognizes. The two Unicode line
 * separators may not appear in a regex literal, so the class is assembled.
 */
const LINE_TERMINATOR = new RegExp(
  `\\r\\n|[\\n\\r\\v\\f\\x1c\\x1d\\x1e\\x85${String.fromCharCode(8232, 8233)}]`,
  "g"
)

function errnoCode(error: unknown): string | undefined {
  return (error as NodeJS.ErrnoException | null)?.code
}

function isMissing(error: unknown): boolean {
  const code = errnoCode(error)
  return code === "ENOENT" || code === "ENOTDIR"
}

function fileTypeOf(filePath: string): FileType {
  const extension = path.posix.extname(filePath.replaceAll("\\", "/"))
  return EXTENSION_FILE_TYPES[extension.toLowerCase()] ?? "text"
}

async function readBytes(resolved: string): Promise<Buffer> {
  const handle = await open(resolved, READ_FLAGS)
  try {
    return await handle.readFile()
  } finally {
    await handle.close()
  }
}

async function writeBytes(
  resolved: string,
  flags: number,
  content: string | Uint8Array
): Promise<void> {
  const handle = await open(resolved, flags, FILE_MODE)
  try {
    await handle.writeFile(content)
  } finally {
    await handle.close()
  }
}

function splitLinesKeepEnds(content: string): string[] {
  const lines: string[] = []
  let start = 0
  for (const match of content.matchAll(LINE_TERMINATOR)) {
    const end = match.index + match[0].length
    lines.push(content.slice(start, end))
    start = end
  }
  if (start < content.length) lines.push(content.slice(start))
  return lines
}

function normalizeBound(value: number): number {
  return Number.isFinite(value) ? Math.max(Math.trunc(value), 0) : 0
}

function normalizeNewlines(value: string): string {
  return value.replaceAll("\r\n", "\n").replaceAll("\r", "\n")
}

function sliceReadResponse(
  content: string,
  requestedOffset: number,
  requestedLimit: number
): ReadResult {
  const offset = normalizeBound(requestedOffset)
  const limit = normalizeBound(requestedLimit)

  if (limit === 0) {
    return {
      fileData: { content: "", encoding: "utf-8" },
      noLinesRequested: true,
    }
  }

  const lines = splitLinesKeepEnds(content)
  const totalLines = lines.length
  if (offset >= totalLines) {
    return {
      error: `Line offset ${offset} exceeds file length (${totalLines} lines)`,
    }
  }

  const endLine = Math.min(offset + limit, totalLines)
  const window = normalizeNewlines(lines.slice(offset, endLine).join(""))
  const sliced: ReadResult = {
    fileData: { content: window, encoding: "utf-8" },
    totalLines,
    startLine: offset + 1,
    endLine,
  }
  return endLine < totalLines ? { ...sliced, nextOffset: endLine } : sliced
}

type Replacement =
  | { readonly content: string; readonly occurrences: number }
  | { readonly error: string }

function countOccurrences(content: string, needle: string): number {
  let occurrences = 0
  content.replaceAll(needle, () => {
    occurrences += 1
    return ""
  })
  return occurrences
}

function performStringReplacement(
  content: string,
  oldString: string,
  newString: string,
  replaceAll: boolean
): Replacement {
  let occurrences = 0
  // A replacer function rather than a plain string: it counts in the same
  // pass, and `$&`-style sequences in `newString` stay literal.
  const replaced = content.replaceAll(oldString, () => {
    occurrences += 1
    return newString
  })

  if (occurrences === 0) {
    const stripped = oldString.slice(0, -1)
    if (
      oldString.endsWith("\n") &&
      oldString.length > 1 &&
      content.endsWith(stripped)
    ) {
      const strippedCount = countOccurrences(content, stripped)
      if (strippedCount === 1) {
        return {
          error:
            "Error: old_string ends with a newline, but the file does not " +
            "end with a newline. Retry with the trailing newline removed " +
            "from old_string (and from new_string if it also ends with a " +
            "newline).",
        }
      }
      return {
        error:
          "Error: old_string ends with a newline, but the file does not end " +
          "with a newline. With the trailing newline removed, old_string " +
          `would appear ${strippedCount} times in the file. Retry with the ` +
          "trailing newline removed and add surrounding context so the " +
          "match is unique.",
      }
    }
    return { error: `Error: String not found in file: '${oldString}'` }
  }

  if (occurrences > 1 && !replaceAll) {
    return {
      error:
        `Error: String '${oldString}' appears ${occurrences} times in file. ` +
        "Use replace_all=True to replace all instances, or provide a more " +
        "specific string with surrounding context.",
    }
  }

  return { content: replaced, occurrences }
}

function entryFor(entryPath: string, stats: Stats): FileInfo {
  const modifiedAt = stats.mtime.toISOString()
  return stats.isDirectory()
    ? { path: `${entryPath}/`, isDir: true, size: 0, modifiedAt }
    : { path: entryPath, isDir: false, size: stats.size, modifiedAt }
}

async function statChild(
  child: string,
  errors: string[]
): Promise<Stats | null | "reported"> {
  try {
    return await stat(child)
  } catch (error) {
    const code = errnoCode(error)
    if (code !== undefined && IGNORED_STAT_CODES.has(code)) return null
    errors.push(`child error: cannot stat '${child}': ${errorMessage(error)}`)
    return "reported"
  }
}

/**
 * Report an entry that is neither a file nor a directory only when it is an
 * unresolvable symlink loop: a dangling link is not an entry, and not a
 * failure the agent can act on either.
 */
async function reportUnresolvableChild(
  child: string,
  errors: string[]
): Promise<void> {
  try {
    if ((await lstat(child)).isSymbolicLink()) await realpath(child)
  } catch (error) {
    if (errnoCode(error) === "ELOOP") {
      errors.push(
        `child error: cannot resolve '${child}': ${errorMessage(error)}`
      )
    }
  }
}

export async function ls(
  defaultDir: string,
  target: string
): Promise<LsResult> {
  let dirPath: string
  try {
    dirPath = resolvePath(defaultDir, target)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  try {
    if (!(await stat(dirPath)).isDirectory()) {
      return { error: `Path '${target}': not_a_directory` }
    }
  } catch (error) {
    if (isMissing(error)) return { error: `Path '${target}': path_not_found` }
    return { error: errorMessage(error) }
  }

  const entries: FileInfo[] = []
  const errors: string[] = []

  let names: string[]
  try {
    names = await readdir(dirPath)
  } catch (error) {
    return { error: errorMessage(error), entries }
  }

  for (const name of names) {
    const child = path.join(dirPath, name)

    const stats = await statChild(child, errors)
    if (stats === "reported") continue
    if (stats === null || (!stats.isFile() && !stats.isDirectory())) {
      await reportUnresolvableChild(child, errors)
      continue
    }

    entries.push(entryFor(child, stats))
  }

  entries.sort((left, right) => (left.path < right.path ? -1 : 1))
  errors.sort()
  return errors.length > 0 ? { error: errors.join("\n"), entries } : { entries }
}

export async function read(
  defaultDir: string,
  filePath: string,
  options?: ReadOptions
): Promise<ReadResult> {
  let resolved: string
  try {
    resolved = resolvePath(defaultDir, filePath)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  let stats: Stats
  try {
    stats = await stat(resolved)
  } catch (error) {
    if (isMissing(error)) return { error: `File '${filePath}' not found` }
    return { error: errorMessage(error) }
  }
  if (!stats.isFile()) return { error: `File '${filePath}' not found` }

  const fileType = fileTypeOf(filePath)
  if (fileType === "video" && stats.size > MAX_VIDEO_INPUT_BYTES) {
    return {
      error: `Video file exceeds maximum input size of ${MAX_VIDEO_INPUT_BYTES} bytes`,
    }
  }

  let raw: Buffer
  try {
    raw = await readBytes(resolved)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  if (fileType !== "text") {
    return { fileData: { content: raw.toString("base64"), encoding: "base64" } }
  }

  let content: string
  try {
    content = new TextDecoder("utf-8", { fatal: true }).decode(raw)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  if (content.trim() === "") {
    return { fileData: { content: EMPTY_CONTENT_WARNING, encoding: "utf-8" } }
  }

  return sliceReadResponse(
    content,
    options?.offset ?? 0,
    options?.limit ?? DEFAULT_READ_LIMIT
  )
}

export async function write(
  defaultDir: string,
  filePath: string,
  content: string
): Promise<WriteResult> {
  let resolved: string
  try {
    resolved = resolvePath(defaultDir, filePath)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  try {
    await mkdir(path.dirname(resolved), { recursive: true })
    await writeBytes(resolved, CREATE_FLAGS, content)
  } catch (error) {
    return { error: errorMessage(error) }
  }
  return { path: resolved }
}

export async function edit(
  defaultDir: string,
  filePath: string,
  oldString: string,
  newString: string,
  replaceAll = false
): Promise<EditResult> {
  let resolved: string
  try {
    resolved = resolvePath(defaultDir, filePath)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  let content: string
  try {
    if (!(await stat(resolved)).isFile()) {
      return { error: `Error: File '${filePath}' not found` }
    }
    content = new TextDecoder("utf-8", { fatal: true }).decode(
      await readBytes(resolved)
    )
  } catch (error) {
    if (isMissing(error))
      return { error: `Error: File '${filePath}' not found` }
    return { error: errorMessage(error) }
  }

  const result = performStringReplacement(
    content,
    normalizeNewlines(oldString),
    normalizeNewlines(newString),
    replaceAll
  )
  if ("error" in result) return { error: result.error }

  try {
    await writeBytes(resolved, TRUNCATE_FLAGS, result.content)
  } catch (error) {
    return { error: errorMessage(error) }
  }
  return { path: resolved, occurrences: result.occurrences }
}

export async function remove(
  defaultDir: string,
  filePath: string
): Promise<DeleteResult> {
  let resolved: string
  try {
    resolved = resolvePath(defaultDir, filePath)
  } catch (error) {
    return { error: errorMessage(error) }
  }

  try {
    if ((await lstat(resolved)).isDirectory()) {
      await rm(resolved, { recursive: true })
    } else {
      await unlink(resolved)
    }
  } catch (error) {
    if (isMissing(error)) return { error: `Error: '${filePath}' not found` }
    return { error: errorMessage(error) }
  }
  return { path: resolved }
}

export async function uploadFiles(
  defaultDir: string,
  files: readonly UploadFile[]
): Promise<FileUploadResponse[]> {
  const responses: FileUploadResponse[] = []
  for (const file of files) {
    try {
      const resolved = resolvePath(defaultDir, file.path)
      await mkdir(path.dirname(resolved), { recursive: true })
      await writeBytes(resolved, CREATE_FLAGS, file.content)
      responses.push({ path: resolved })
    } catch (error) {
      // A path that never resolved has no real form to report, so the
      // response carries the requested one back.
      responses.push({ path: file.path, error: toFileOperationError(error) })
    }
  }
  return responses
}

export async function downloadFiles(
  defaultDir: string,
  paths: readonly string[]
): Promise<FileDownloadResponse[]> {
  const responses: FileDownloadResponse[] = []
  for (const filePath of paths) {
    try {
      const resolved = resolvePath(defaultDir, filePath)
      let isDirectory = false
      try {
        isDirectory = (await stat(resolved)).isDirectory()
      } catch (error) {
        // A path that is not there is left to the read below, which reports
        // it as `file_not_found` rather than as a failed directory probe.
        if (!isMissing(error)) throw error
      }
      if (isDirectory) {
        responses.push({ path: resolved, error: "is_directory" })
        continue
      }
      responses.push({ path: resolved, content: await readBytes(resolved) })
    } catch (error) {
      // A path that never resolved has no real form to report, so the
      // response carries the requested one back.
      responses.push({ path: filePath, error: toFileOperationError(error) })
    }
  }
  return responses
}
