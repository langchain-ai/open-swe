import path from "node:path"

import type { FileOperationError } from "./types.ts"

export class InvalidPathError extends Error {
  readonly candidate: string

  constructor(candidate: string) {
    super("path is not a usable filesystem path")
    this.name = "InvalidPathError"
    this.candidate = candidate
  }
}

/**
 * Resolve `candidate` to an absolute path, taking a relative one as relative to
 * `defaultDir`.
 *
 * There is no containment check. The backend reaches the whole filesystem, as
 * the user does: `execute` runs real shell commands, so a fenced file API only
 * produced the confusing split where the agent could `cat` a file it could not
 * `read`. The request signature is the boundary.
 *
 * Symlinks are deliberately not resolved, so a path stays the one the caller
 * asked for and matches what the shell prints (`/var/x`, not `/private/var/x`).
 */
export function resolvePath(defaultDir: string, candidate: string): string {
  if (candidate.length === 0 || candidate.includes("\0")) {
    throw new InvalidPathError(candidate)
  }
  return path.resolve(defaultDir, candidate)
}

export function toFileOperationError(
  error: unknown
): FileOperationError | string {
  if (error instanceof InvalidPathError) return "invalid_path"
  const code = (error as NodeJS.ErrnoException | null)?.code
  switch (code) {
    case "ENOENT":
    case "ENOTDIR":
      return "file_not_found"
    case "EACCES":
    case "EPERM":
      return "permission_denied"
    case "EISDIR":
      return "is_directory"
    default:
      return error instanceof Error ? error.message : String(error)
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof InvalidPathError) {
    return `Error: ${error.candidate} is not a usable filesystem path`
  }
  const code = (error as NodeJS.ErrnoException | null)?.code
  switch (code) {
    case "ENOENT":
    case "ENOTDIR":
      return "Error: file not found"
    case "EACCES":
    case "EPERM":
      return "Error: permission denied"
    case "EISDIR":
      return "Error: path is a directory"
    default:
      return `Error: ${error instanceof Error ? error.message : String(error)}`
  }
}
