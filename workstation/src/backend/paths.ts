import { realpath } from "node:fs/promises"
import path from "node:path"

import type { FileOperationError } from "./types.ts"

export class PathOutsideRootError extends Error {
  readonly candidate: string

  constructor(candidate: string) {
    super("path is outside the workstation root")
    this.name = "PathOutsideRootError"
    this.candidate = candidate
  }
}

export function isInside(root: string, target: string): boolean {
  return target === root || target.startsWith(root + path.sep)
}

function isNotFound(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | null)?.code
  return code === "ENOENT" || code === "ENOTDIR"
}

/**
 * The realpath of the deepest ancestor of `target` that exists, plus the
 * segments below it that do not. A write to a file that is not there yet still
 * has to be containment-checked, and `realpath` fails outright on a missing
 * path.
 */
async function deepestExisting(
  target: string
): Promise<{ real: string; rest: string[] }> {
  const missing: string[] = []
  let current = target
  for (;;) {
    try {
      return { real: await realpath(current), rest: missing.reverse() }
    } catch (error) {
      if (!isNotFound(error)) throw error
      const parent = path.dirname(current)
      if (parent === current) {
        return { real: current, rest: missing.reverse() }
      }
      missing.push(path.basename(current))
      current = parent
    }
  }
}

/**
 * Resolve `candidate` (absolute, or relative to the root) to an absolute path
 * inside `rootDir`, following symlinks so a link cannot point out of the root.
 *
 * This bounds what a *well-behaved* caller reaches; it is not a sandbox. The
 * root is a directory on the user's own machine and `execute` runs real
 * commands in it, so anything already able to write inside the root can escape
 * regardless. Containment exists to stop accidental and model-driven wandering
 * outside the project, and to make a request naming another project fail loudly.
 */
export async function resolveWithinRoot(
  rootDir: string,
  candidate: string
): Promise<string> {
  if (candidate.length === 0 || candidate.includes("\0")) {
    throw new PathOutsideRootError(candidate)
  }
  const root = await realpath(rootDir)
  const { real, rest } = await deepestExisting(path.resolve(root, candidate))
  const resolved = rest.length > 0 ? path.join(real, ...rest) : real
  if (!isInside(root, resolved)) {
    throw new PathOutsideRootError(candidate)
  }
  return resolved
}

export function toFileOperationError(
  error: unknown
): FileOperationError | string {
  if (error instanceof PathOutsideRootError) return "invalid_path"
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
  if (error instanceof PathOutsideRootError) {
    return `Error: ${error.candidate} is outside the workstation root`
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
