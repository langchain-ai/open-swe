import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import { agentsApi } from "@/features/agents/lib/api"

export interface WorkspaceEntry {
  path: string
  kind: "file" | "directory"
  ignored?: boolean
}

export interface WorkspaceFileIndex {
  paths: Array<string>
  truncated: boolean
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

/** Every non-ignored file in the local session or cloud sandbox. */
export function listWorkspaceFiles(
  target: TerminalTarget
): Promise<WorkspaceFileIndex> {
  if (target.kind === "cloud")
    return agentsApi.getThreadFileIndex(target.threadId)
  const desktop = window.openSweDesktop
  if (!desktop) return Promise.reject(new Error("Desktop app required"))
  return desktop.listWorkspaceFiles(target.sessionId)
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
  // Only the newest request per directory may apply, so an older refresh
  // finishing last cannot overwrite a newer listing.
  const generations = useRef(new Map<string, number>())
  const indexGeneration = useRef(0)
  const [index, setIndex] = useState<ReadonlyArray<string> | null>(null)

  const load = useCallback(async (directory: string, force = false) => {
    if (!force && requested.current.has(directory)) return
    requested.current.add(directory)
    const generation = (generations.current.get(directory) ?? 0) + 1
    generations.current.set(directory, generation)
    const isLatest = () => generations.current.get(directory) === generation
    setPending((count) => count + 1)
    try {
      const result = await readWorkspacePath(targetRef.current, directory)
      if (result.kind !== "directory" || !isLatest()) return
      setDirectories((previous) =>
        new Map(previous).set(directory, result.entries)
      )
      setError(null)
    } catch (cause) {
      if (!isLatest()) return
      requested.current.delete(directory)
      if (directory === "" || !force) setError(errorText(cause))
    } finally {
      setPending((count) => count - 1)
    }
  }, [])

  /** Fetches every file path so search covers folders that were never expanded. */
  const loadIndex = useCallback(async () => {
    const generation = ++indexGeneration.current
    setPending((count) => count + 1)
    try {
      const result = await listWorkspaceFiles(targetRef.current)
      if (generation === indexGeneration.current) setIndex(result.paths)
    } catch (cause) {
      if (generation === indexGeneration.current) setError(errorText(cause))
    } finally {
      setPending((count) => count - 1)
    }
  }, [])

  const clearIndex = useCallback(() => {
    indexGeneration.current += 1
    setIndex(null)
  }, [])

  useEffect(() => {
    void load("")
  }, [load])

  const entries = useMemo(() => {
    const result: Array<WorkspaceEntry> = []
    const seen = new Set<string>()
    const add = (entry: WorkspaceEntry) => {
      if (seen.has(entry.path)) return
      seen.add(entry.path)
      result.push(entry)
    }
    const visit = (directory: string) => {
      for (const entry of directories.get(directory) ?? []) {
        add(entry)
        if (entry.kind === "directory") visit(entry.path)
      }
    }
    visit("")
    for (const path of index ?? []) {
      const segments = path.split("/")
      for (let depth = 1; depth < segments.length; depth++)
        add({ path: segments.slice(0, depth).join("/"), kind: "directory" })
      add({ path, kind: "file" })
    }
    return result
  }, [directories, index])

  const refresh = useCallback(() => {
    for (const directory of requested.current) void load(directory, true)
    if (index !== null) void loadIndex()
  }, [index, load, loadIndex])

  return {
    entries,
    load,
    loadIndex,
    clearIndex,
    refresh,
    error,
    isPending: pending > 0,
    ready: directories.has(""),
  }
}
