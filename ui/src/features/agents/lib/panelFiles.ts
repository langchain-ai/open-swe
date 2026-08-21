import type { GitStatus } from "@pierre/trees"
import type { PanelFile } from "@/components/diff/DiffFilesView"
import type { ThreadPrDiffFile } from "@/features/agents/lib/api"

function prFileStatus(file: ThreadPrDiffFile): GitStatus {
  if (file.status === "added") return "added"
  if (file.status === "removed") return "deleted"
  return "modified"
}

export function commonDirPrefix(paths: Array<string>): string {
  const first = paths[0]
  if (paths.length === 0 || first === undefined) return ""
  const base = first.split("/").slice(0, -1)
  let depth = base.length
  for (const path of paths) {
    const segments = path.split("/").slice(0, -1)
    let i = 0
    while (i < depth && i < segments.length && segments[i] === base[i]) i++
    depth = i
  }
  return depth === 0 ? "" : `${base.slice(0, depth).join("/")}/`
}

export function toPanelFiles(
  diffFiles: Array<ThreadPrDiffFile>
): Array<PanelFile> {
  const prefix = commonDirPrefix(diffFiles.map((file) => file.path))
  return diffFiles.map((file) => ({
    filePath: file.path,
    treePath:
      prefix && file.path.startsWith(prefix)
        ? file.path.slice(prefix.length)
        : file.path,
    additions: file.additions,
    deletions: file.deletions,
    originalContent: file.originalContent ?? "",
    modifiedContent: file.modifiedContent ?? "",
    status: prFileStatus(file),
    unrenderable: file.unrenderable,
  }))
}
