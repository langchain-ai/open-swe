import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import { agentsApi } from "@/features/agents/lib/api"

export interface WorkspaceEntry {
  path: string
  kind: "file" | "directory"
  ignored?: boolean
}

export type WorkspacePath =
  | { kind: "directory"; entries: Array<WorkspaceEntry> }
  | {
      kind: "file"
      contents: string
      binary: boolean
      truncated: boolean
      size: number
    }

export function workspaceTargetKey(target: TerminalTarget): string {
  return target.kind === "local"
    ? `local:${target.sessionId}`
    : `cloud:${target.threadId}`
}

/** Lists a directory or reads a file from the local session or cloud sandbox. */
export function readWorkspacePath(
  target: TerminalTarget,
  relativePath: string
): Promise<WorkspacePath> {
  if (target.kind === "cloud")
    return agentsApi.getThreadPath(target.threadId, relativePath)
  const desktop = window.openSweDesktop
  if (!desktop) return Promise.reject(new Error("Desktop app required"))
  return desktop.readWorkspacePath({
    localSessionId: target.sessionId,
    relativePath,
  })
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : "Unable to load folder."
}

export function useWorkspaceFile(
  target: TerminalTarget,
  relativePath: string | null
) {
  return useQuery({
    queryKey: ["workspace-path", workspaceTargetKey(target), relativePath],
    queryFn: () => readWorkspacePath(target, relativePath ?? ""),
    enabled: relativePath !== null,
    retry: false,
  })
}

/** Loads only requested directories; collapsing a folder keeps its children cached. */
export function useDirectoryEntries(target: TerminalTarget) {
  const targetRef = useRef(target)
  const requested = useRef(new Set<string>())
  const [directories, setDirectories] = useState(
    new Map<string, ReadonlyArray<WorkspaceEntry>>()
  )
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(0)

  const load = useCallback(async (directory: string, force = false) => {
    if (!force && requested.current.has(directory)) return
    requested.current.add(directory)
    setPending((count) => count + 1)
    try {
      const result = await readWorkspacePath(targetRef.current, directory)
      if (result.kind !== "directory") return
      setDirectories((previous) =>
        new Map(previous).set(directory, result.entries)
      )
      setError(null)
    } catch (cause) {
      requested.current.delete(directory)
      if (directory === "" || !force) setError(errorText(cause))
    } finally {
      setPending((count) => count - 1)
    }
  }, [])

  useEffect(() => {
    void load("")
  }, [load])

  const entries = useMemo(() => {
    const result: Array<WorkspaceEntry> = []
    const visit = (directory: string) => {
      for (const entry of directories.get(directory) ?? []) {
        result.push(entry)
        if (entry.kind === "directory") visit(entry.path)
      }
    }
    visit("")
    return result
  }, [directories])

  const refresh = useCallback(() => {
    for (const directory of requested.current) void load(directory, true)
  }, [load])

  return {
    entries,
    load,
    refresh,
    error,
    isPending: pending > 0,
    ready: directories.has(""),
  }
}
