/**
 * Search operations (`grep`, `glob`) over a directory on this machine.
 *
 * Pattern matching follows the deep agents include-glob contract, which is not
 * shell globbing: a pattern without `/` matches the basename at any depth, a
 * pattern with `/` matches the search-root-relative path, a leading `/` anchors
 * (and narrows) to the search root, and leading-dot names match only when the
 * pattern segment itself starts with `.`.
 */

import type { Dirent, Stats } from "node:fs"
import { readFile, readdir, stat } from "node:fs/promises"
import path from "node:path"

import picomatch from "picomatch"

import { errorMessage, resolvePath } from "./paths.ts"
import type {
  ContextLine,
  FileInfo,
  GlobResult,
  GrepMatch,
  GrepOptions,
  GrepResult,
} from "./types.ts"

const GLOB_TIME_BUDGET_MS = 5_000
const GREP_TIME_BUDGET_MS = 15_000
const MAX_WALK_ENTRIES = 100_000
const MAX_SEARCHED_FILE_BYTES = 10 * 1024 * 1024
const MAX_BRACE_EXPANSIONS = 1000

type GlobMatcher = (relativePath: string) => boolean

type CompiledGlob =
  | { readonly ok: true; readonly matcher: GlobMatcher }
  | { readonly ok: false; readonly error: string }

function detail(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function braceExpansions(
  pattern: string,
  start: number,
  depth: number
): {
  count: number
  index: number
} {
  let alternatives = 0
  let sequence = 1
  let index = start
  while (index < pattern.length) {
    const char = pattern[index]
    if (char === "\\") {
      index += 2
      continue
    }
    if (char === "{") {
      const inner = braceExpansions(pattern, index + 1, depth + 1)
      const body = pattern.slice(
        index + 1,
        Math.max(index + 1, inner.index - 1)
      )
      sequence *= rangeExpansions(body) ?? inner.count
      index = inner.index
      continue
    }
    if (char === "}" && depth > 0) {
      index += 1
      break
    }
    if (char === "," && depth > 0) {
      alternatives += sequence
      sequence = 1
      index += 1
      continue
    }
    index += 1
  }
  return { count: alternatives + sequence, index }
}

/** Expansion count of a bash-style `{1..9}` / `{a..z}` brace body. */
function rangeExpansions(body: string): number | null {
  const numeric = /^(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$/.exec(body)
  if (numeric !== null) {
    const from = Number(numeric[1])
    const to = Number(numeric[2])
    const step = Math.abs(Number(numeric[3] ?? 1)) || 1
    return Math.floor(Math.abs(to - from) / step) + 1
  }
  const alpha = /^([a-zA-Z])\.\.([a-zA-Z])$/.exec(body)
  if (alpha !== null) {
    return Math.abs(alpha[2]!.charCodeAt(0) - alpha[1]!.charCodeAt(0)) + 1
  }
  return null
}

function compileGlob(pattern: string): CompiledGlob {
  if (pattern.replace(/\\/g, "/").split("/").includes("..")) {
    return {
      ok: false,
      error: `Path traversal not allowed in glob pattern '${pattern}'`,
    }
  }
  if (braceExpansions(pattern, 0, 0).count > MAX_BRACE_EXPANSIONS) {
    return {
      ok: false,
      error: `Invalid glob pattern '${pattern}': Pattern limit exceeded the limit of ${MAX_BRACE_EXPANSIONS}`,
    }
  }
  // Anchoring is decided from the original pattern so a leading `/` narrows to
  // the search root instead of collapsing to a basename match once stripped.
  const anchored = pattern.includes("/")
  let isMatch: GlobMatcher
  try {
    isMatch = picomatch(pattern.replace(/^\/+/, ""), {
      dot: false,
      nobrace: false,
      noextglob: true,
      nonegate: true,
    })
  } catch (error) {
    return {
      ok: false,
      error: `Invalid glob pattern '${pattern}': ${detail(error)}`,
    }
  }
  return {
    ok: true,
    matcher: (relativePath) =>
      isMatch(
        anchored
          ? relativePath
          : relativePath.slice(relativePath.lastIndexOf("/") + 1)
      ),
  }
}

interface WalkedFile {
  readonly absolutePath: string
  readonly relativePath: string
}

interface WalkLimits {
  readonly deadline: number
  readonly maxEntries: number
}

interface WalkState {
  budgetExhausted: boolean
  readonly unreadable: string[]
}

function isPermissionError(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | null)?.code
  return code === "EACCES" || code === "EPERM"
}

function toRelative(root: string, absolutePath: string): string {
  return path.relative(root, absolutePath).split(path.sep).join("/")
}

/**
 * Whether a symlink entry should be reported as a regular file. A link to a
 * directory is never descended into, which is what keeps the walk free of
 * symlink cycles.
 */
async function isFileLink(
  absolutePath: string,
  state: WalkState
): Promise<boolean> {
  try {
    return (await stat(absolutePath)).isFile()
  } catch (error) {
    // A dangling or looping link resolves to no file, so there is nothing to
    // report; a link we may not resolve can hide files a narrower pattern
    // would never surface, so that one counts as unreadable.
    if (isPermissionError(error)) state.unreadable.push(absolutePath)
    return false
  }
}

async function* walkFiles(
  root: string,
  limits: WalkLimits,
  state: WalkState
): AsyncGenerator<WalkedFile> {
  const stack: string[] = [root]
  let visited = 0
  while (stack.length > 0) {
    const dir = stack.pop()
    if (dir === undefined) break
    let entries: Dirent[]
    try {
      entries = await readdir(dir, { withFileTypes: true })
    } catch (error) {
      if (dir === root) throw error
      state.unreadable.push(dir)
      continue
    }
    entries.sort((left, right) => (left.name < right.name ? -1 : 1))
    const subdirectories: string[] = []
    for (const entry of entries) {
      visited += 1
      if (visited > limits.maxEntries || Date.now() > limits.deadline) {
        state.budgetExhausted = true
        return
      }
      const absolutePath = path.join(dir, entry.name)
      if (entry.isSymbolicLink()) {
        if (await isFileLink(absolutePath, state)) {
          yield { absolutePath, relativePath: toRelative(root, absolutePath) }
        }
      } else if (entry.isDirectory()) {
        subdirectories.push(absolutePath)
      } else if (entry.isFile()) {
        yield { absolutePath, relativePath: toRelative(root, absolutePath) }
      }
    }
    for (const subdirectory of subdirectories.reverse()) {
      stack.push(subdirectory)
    }
  }
}

/** A bare `/` names the search root itself, matching the pattern contract. */
function resolveSearchRoot(
  defaultDir: string,
  candidate: string | undefined
): string {
  // An unspecified search path means `defaultDir`; the middleware sends `None`
  // for an unscoped search rather than a placeholder root. Every actual path,
  // `/` included, resolves literally.
  if (candidate === undefined || candidate === "") {
    return defaultDir
  }
  return resolvePath(defaultDir, candidate)
}

type PathKind =
  | { readonly kind: "file"; readonly stats: Stats }
  | { readonly kind: "directory" }
  | { readonly kind: "missing" }
  | { readonly kind: "error"; readonly message: string }

async function classify(absolutePath: string): Promise<PathKind> {
  try {
    const stats = await stat(absolutePath)
    if (stats.isDirectory()) return { kind: "directory" }
    return stats.isFile() ? { kind: "file", stats } : { kind: "missing" }
  } catch (error) {
    const code = (error as NodeJS.ErrnoException | null)?.code
    if (code === "ENOENT" || code === "ENOTDIR") return { kind: "missing" }
    return { kind: "error", message: errorMessage(error) }
  }
}

async function toFileInfo(absolutePath: string): Promise<FileInfo> {
  try {
    const stats = await stat(absolutePath)
    return {
      path: absolutePath,
      isDir: false,
      size: stats.size,
      modifiedAt: new Date(stats.mtimeMs).toISOString(),
    }
  } catch {
    // The entry was seen during the walk but can no longer be stat'd; the path
    // itself is still a true result, so report it without metadata.
    return { path: absolutePath, isDir: false }
  }
}

export async function glob(
  defaultDir: string,
  pattern: string,
  searchPath?: string
): Promise<GlobResult> {
  const compiled = compileGlob(pattern)
  if (!compiled.ok) return { error: compiled.error, truncated: false }

  let base: string
  try {
    base = resolveSearchRoot(defaultDir, searchPath)
  } catch (error) {
    return { error: errorMessage(error), matches: [], truncated: false }
  }

  const baseKind = await classify(base)
  if (baseKind.kind === "error") {
    return { error: baseKind.message, matches: [], truncated: false }
  }
  if (baseKind.kind !== "directory") return { matches: [], truncated: false }

  const state: WalkState = { budgetExhausted: false, unreadable: [] }
  const limits: WalkLimits = {
    deadline: Date.now() + GLOB_TIME_BUDGET_MS,
    maxEntries: MAX_WALK_ENTRIES,
  }
  const matches: FileInfo[] = []
  try {
    for await (const file of walkFiles(base, limits, state)) {
      if (!compiled.matcher(file.relativePath)) continue
      matches.push(await toFileInfo(file.absolutePath))
    }
  } catch (error) {
    matches.sort((left, right) => (left.path < right.path ? -1 : 1))
    return { error: errorMessage(error), matches, truncated: false }
  }
  matches.sort((left, right) => (left.path < right.path ? -1 : 1))

  if (state.budgetExhausted) {
    return { matches, truncated: true, truncationReason: "budget" }
  }
  if (state.unreadable.length > 0) {
    return { matches, truncated: true, truncationReason: "unreadable" }
  }
  return { matches, truncated: false }
}

interface RawMatch {
  readonly path: string
  readonly line: number
  readonly text: string
}

type TextRead =
  | { readonly ok: true; readonly content: string }
  | { readonly ok: false; readonly binary: boolean; readonly message: string }

async function readText(absolutePath: string): Promise<TextRead> {
  let bytes: Buffer
  try {
    bytes = await readFile(absolutePath)
  } catch (error) {
    return { ok: false, binary: false, message: errorMessage(error) }
  }
  try {
    return {
      ok: true,
      content: new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    }
  } catch (error) {
    return { ok: false, binary: true, message: detail(error) }
  }
}

function splitLines(content: string): string[] {
  const lines = content.split(/\r\n|\n|\r/)
  if (lines[lines.length - 1] === "") lines.pop()
  return lines
}

/**
 * Attach the requested surrounding lines to each match. A line that is itself a
 * match, or that contains the pattern but was dropped by the match cap, is
 * never repeated as context. Returns the matched files whose context could not
 * be re-read.
 */
async function withContext(
  raw: readonly RawMatch[],
  contextLines: number,
  pattern: string
): Promise<{ matches: GrepMatch[]; unreadable: string[] }> {
  const byPath = new Map<string, RawMatch[]>()
  for (const match of raw) {
    const existing = byPath.get(match.path)
    if (existing === undefined) byPath.set(match.path, [match])
    else existing.push(match)
  }

  const unreadable: string[] = []
  const contextByPath = new Map<string, ContextLine[]>()
  for (const [filePath, fileMatches] of byPath) {
    const read = await readText(filePath)
    if (!read.ok) {
      unreadable.push(filePath)
      contextByPath.set(filePath, [])
      continue
    }
    const lines = splitLines(read.content)
    const matchLines = new Set(fileMatches.map((match) => match.line))
    const wanted = new Set<number>()
    for (const match of fileMatches) {
      const from = Math.max(1, match.line - contextLines)
      const to = Math.min(lines.length, match.line + contextLines)
      for (let line = from; line <= to; line += 1) wanted.add(line)
    }
    const items: ContextLine[] = []
    for (const line of [...wanted].sort((left, right) => left - right)) {
      const text = lines[line - 1] ?? ""
      if (matchLines.has(line) || text.includes(pattern)) continue
      items.push({ line, text })
    }
    contextByPath.set(filePath, items)
  }

  const matches = raw.map((match): GrepMatch => {
    const items = contextByPath.get(match.path) ?? []
    return {
      path: match.path,
      line: match.line,
      text: match.text,
      contextBefore: items.filter(
        (item) =>
          item.line >= match.line - contextLines && item.line < match.line
      ),
      contextAfter: items.filter(
        (item) =>
          item.line > match.line && item.line <= match.line + contextLines
      ),
    }
  })
  return { matches, unreadable }
}

function joinErrors(
  fileErrors: readonly string[],
  unreadableDirs: readonly string[],
  contextError: string | undefined
): string | undefined {
  const parts: string[] = []
  if (fileErrors.length > 0) {
    parts.push(
      `One or more files could not be fully searched:\n${fileErrors.join("\n")}`
    )
  }
  if (unreadableDirs.length > 0) {
    parts.push(
      `Error: could not search ${unreadableDirs.length} directory(ies) (unreadable): ${[...unreadableDirs].sort().join(", ")}`
    )
  }
  if (contextError !== undefined) parts.push(contextError)
  return parts.length > 0 ? parts.join("\n") : undefined
}

export async function grep(
  defaultDir: string,
  pattern: string,
  options: GrepOptions = {}
): Promise<GrepResult> {
  const contextLines = options.contextLines ?? 0
  if (!Number.isInteger(contextLines) || contextLines < 0) {
    return {
      error: `Error: contextLines must be a non-negative integer, got ${contextLines}`,
      matches: [],
      truncated: false,
    }
  }

  let includeMatcher: GlobMatcher | undefined
  if (options.glob !== undefined) {
    const compiled = compileGlob(options.glob)
    if (!compiled.ok) {
      return { error: compiled.error, matches: [], truncated: false }
    }
    includeMatcher = compiled.matcher
  }

  let base: string
  try {
    base = resolveSearchRoot(defaultDir, options.path)
  } catch (error) {
    return { error: errorMessage(error), matches: [], truncated: false }
  }

  const baseKind = await classify(base)
  if (baseKind.kind === "error") {
    return { error: baseKind.message, matches: [], truncated: false }
  }
  if (baseKind.kind === "missing") return { matches: [], truncated: false }

  const state: WalkState = { budgetExhausted: false, unreadable: [] }
  const limits: WalkLimits = {
    deadline: Date.now() + GREP_TIME_BUDGET_MS,
    maxEntries: MAX_WALK_ENTRIES,
  }
  const raw: RawMatch[] = []
  const fileErrors: string[] = []
  let capped = false

  const scan = async (absolutePath: string, size: number): Promise<void> => {
    if (size > MAX_SEARCHED_FILE_BYTES) return
    const read = await readText(absolutePath)
    if (!read.ok) {
      // An undecodable file is binary and skipped the way ripgrep skips it; a
      // file that would not open is one the caller expected to search.
      if (!read.binary) fileErrors.push(`- ${absolutePath}: ${read.message}`)
      return
    }
    const lines = splitLines(read.content)
    for (const [index, text] of lines.entries()) {
      if (!text.includes(pattern)) continue
      if (options.maxCount !== undefined && raw.length >= options.maxCount) {
        // Exactly `maxCount` matches with nothing left is complete; finding one
        // more without keeping it is what proves the result is truncated.
        capped = true
        return
      }
      raw.push({ path: absolutePath, line: index + 1, text })
    }
  }

  // A single file path searches only that file, so an include glob is
  // irrelevant to it and is not applied.
  if (baseKind.kind === "file") {
    await scan(base, baseKind.stats.size)
  } else {
    try {
      for await (const file of walkFiles(base, limits, state)) {
        if (
          includeMatcher !== undefined &&
          !includeMatcher(file.relativePath)
        ) {
          continue
        }
        const kind = await classify(file.absolutePath)
        if (kind.kind === "error") {
          fileErrors.push(`- ${file.absolutePath}: ${kind.message}`)
          continue
        }
        if (kind.kind !== "file") continue
        await scan(file.absolutePath, kind.stats.size)
        if (capped) break
        if (Date.now() > limits.deadline) {
          state.budgetExhausted = true
          break
        }
      }
    } catch (error) {
      return {
        error: errorMessage(error),
        matches: raw.map(({ path: filePath, line, text }) => ({
          path: filePath,
          line,
          text,
        })),
        truncated: false,
      }
    }
  }

  const truncated = capped || state.budgetExhausted
  if (contextLines === 0) {
    const error = joinErrors(fileErrors, state.unreadable, undefined)
    return error === undefined
      ? { matches: raw, truncated }
      : { error, matches: raw, truncated }
  }

  const { matches, unreadable } = await withContext(raw, contextLines, pattern)
  const contextError =
    unreadable.length > 0
      ? `Error: could not read context for ${unreadable.length} file(s) (non-UTF-8 or unreadable): ${[...unreadable].sort().join(", ")}`
      : undefined
  const error = joinErrors(fileErrors, state.unreadable, contextError)
  return error === undefined
    ? { matches, truncated }
    : { error, matches, truncated }
}
