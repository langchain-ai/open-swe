import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"

import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import { agentsApi } from "@/features/agents/lib/api"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"

export interface WorkspaceEntry {
  path: string
  kind: "file" | "directory"
  ignored?: boolean
}

export interface WorkspaceFileIndex {
  paths: Array<string>
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

/** A short-lived URL serving one file's bytes, or an HTML or Markdown file's folder. */
export interface WorkspacePreviewLink {
  url: string
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

/** Mints a preview URL for a file in the local session or cloud sandbox. */
export async function previewWorkspaceFile(
  target: TerminalTarget,
  relativePath: string
): Promise<WorkspacePreviewLink> {
  if (target.kind === "cloud") {
    const link = await agentsApi.createThreadFilePreview(
      target.threadId,
      relativePath
    )
    return {
      // Absolute, so relative links inside the previewed file resolve against it.
      url: new URL(dashboardApiUrl(link.path), window.location.href).href,
    }
  }
  const desktop = window.openSweDesktop
  if (!desktop?.previewWorkspaceFile)
    throw new Error("Update the desktop app to preview local files.")
  return desktop.previewWorkspaceFile({
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

/** A preview link for the open file; refetching mints a new one, which reloads the preview. */
export function useWorkspacePreview(
  target: TerminalTarget,
  relativePath: string | null,
  enabled: boolean
) {
  return useQuery({
    queryKey: ["workspace-preview", workspaceTargetKey(target), relativePath],
    queryFn: () => previewWorkspaceFile(target, relativePath ?? ""),
    enabled: enabled && relativePath !== null,
    // Opening or returning to a file shows it afresh, through a new link. A
    // focus change must not, since a new link reloads the preview.
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
}

/**
 * Saves a workspace file through a blob URL, which downloads the same way on the
 * web and in the desktop app, whatever scheme the preview link uses.
 */
export async function downloadWorkspaceFile(
  target: TerminalTarget,
  relativePath: string
): Promise<void> {
  const link = await previewWorkspaceFile(target, relativePath)
  const url = URL.createObjectURL(new Blob([await fetchPreviewBytes(link.url)]))
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = relativePath.split("/").pop() ?? "download"
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** The bytes behind a preview link, for viewers that parse the file themselves. */
export async function fetchPreviewBytes(url: string): Promise<ArrayBuffer> {
  // The link's token is the credential; the session cookie is not needed.
  const response = await fetch(url, { credentials: "omit" })
  if (!response.ok)
    throw new Error((await response.text()) || response.statusText)
  return response.arrayBuffer()
}

/** A preview's bytes, parsed once per link by `parse`. */
export function usePreviewData<T>(
  url: string,
  parse: (bytes: ArrayBuffer) => T | Promise<T>
) {
  return useQuery({
    // oxlint-disable-next-line query/exhaustive-deps -- A link names one file, so one parser.
    queryKey: ["workspace-preview-data", url],
    queryFn: async () => parse(await fetchPreviewBytes(url)),
    staleTime: Infinity,
    retry: false,
    // To the viewer's boundary, which offers the file as a download instead.
    throwOnError: true,
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
