import { useQuery } from "@tanstack/react-query"
import { memo, useEffect, useMemo, useRef, useState } from "react"
import {
  CaretDownIcon,
  ChatCircleIcon,
  CheckIcon,
  FolderSimpleIcon,
  MagnifyingGlassIcon,
} from "@phosphor-icons/react"

import type { ReviewDiffFile, ReviewFinding } from "@/lib/api"
import type { ReviewThread } from "@/features/reviews/lib/conversationApi"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import { buildEntries, compareTreePaths, type DiffEntry } from "./diffEntries"
import {
  findingGroupColor,
  isAnchored,
  threadsNeedingAttention,
} from "./findings"
import { InlineCode } from "./inlineCode"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

interface FolderNode {
  kind: "folder"
  name: string
  path: string
  children: Array<TreeNode>
}

interface FileNode {
  kind: "file"
  name: string
  file: ReviewDiffFile
}

type TreeNode = FolderNode | FileNode

/** Folders with a single child folder fold into one row, as GitHub's tree does. */
function buildTree(files: ReadonlyArray<ReviewDiffFile>): Array<TreeNode> {
  const root: FolderNode = { kind: "folder", name: "", path: "", children: [] }
  for (const file of [...files].sort((a, b) =>
    compareTreePaths(a.path, b.path)
  )) {
    const parts = file.path.split("/")
    let folder = root
    parts.slice(0, -1).forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/")
      let next = folder.children.find(
        (child): child is FolderNode =>
          child.kind === "folder" && child.path === path
      )
      if (!next) {
        next = { kind: "folder", name: part, path, children: [] }
        folder.children.push(next)
      }
      folder = next
    })
    folder.children.push({
      kind: "file",
      name: parts.at(-1) ?? file.path,
      file,
    })
  }
  const compact = (node: FolderNode): FolderNode => {
    let current = node
    while (
      current.children.length === 1 &&
      current.children[0]!.kind === "folder"
    ) {
      const only = current.children[0] as FolderNode
      current = {
        ...only,
        name: current.name ? `${current.name}/${only.name}` : only.name,
      }
    }
    return {
      ...current,
      children: current.children.map((child) =>
        child.kind === "folder" ? compact(child) : child
      ),
    }
  }
  const compacted = compact(root)
  return compacted.name ? [compacted] : compacted.children
}

interface Markers {
  findings: Map<string, ReviewFinding["group"]>
  threads: Map<string, number>
}

function markersFor(
  findings: ReadonlyArray<ReviewFinding>,
  threads: ReadonlyArray<ReviewThread>
): Markers {
  const rank = { bug: 0, investigate: 1, informational: 2 } as const
  const byFile = new Map<string, ReviewFinding["group"]>()
  for (const finding of findings) {
    if (finding.status !== "open" || !isAnchored(finding)) continue
    const current = byFile.get(finding.file)
    if (!current || rank[finding.group] < rank[current])
      byFile.set(finding.file, finding.group)
  }
  const threadCount = new Map<string, number>()
  for (const thread of threadsNeedingAttention(threads, findings))
    threadCount.set(thread.path, (threadCount.get(thread.path) ?? 0) + 1)
  return { findings: byFile, threads: threadCount }
}

/** The left rail: every file with what's been read and what needs attention, or the walkthrough's steps. */
export function Navigator({ pr }: { pr: PullRequestRef }) {
  const diff = useQuery(reviewQueries.diff(pr))
  const detail = useQuery(reviewQueries.detail(pr)).data
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  const order = useReviewPage((state) => state.order)
  const setOrder = useReviewPage((state) => state.setOrder)
  const viewed = useReviewPage((state) => state.viewed)
  const files = diff.data?.files
  const walkthrough = detail?.walkthrough ?? null
  const hasWalkthrough = (walkthrough?.steps.length ?? 0) > 0
  const showSteps = order === "guide" && hasWalkthrough
  const viewedCount = files?.filter((file) => viewed.has(file.path)).length ?? 0
  const markers = useMemo(
    () => markersFor(detail?.findings ?? [], conversation?.threads ?? []),
    [detail?.findings, conversation?.threads]
  )

  return (
    <nav aria-label="Files" className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-2 px-3 pt-3 pb-2">
        {hasWalkthrough && (
          <div
            role="group"
            aria-label="Reading order"
            className="flex rounded-md bg-muted p-0.5"
          >
            {(["guide", "files"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={order === value}
                onClick={() => setOrder(value)}
                className={cn(
                  "flex-1 rounded-[5px] py-1 text-[11px] font-medium text-muted-foreground",
                  order === value && "bg-background text-foreground shadow-xs"
                )}
              >
                {value === "guide"
                  ? `Walkthrough · ${walkthrough?.steps.length}`
                  : `Files · ${files?.length ?? "–"}`}
              </button>
            ))}
          </div>
        )}
        {files && (
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground tabular-nums">
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-300"
                style={{
                  width: `${files.length ? (viewedCount / files.length) * 100 : 0}%`,
                }}
              />
            </div>
            {viewedCount}/{files.length} viewed
          </div>
        )}
      </div>
      {!files ? (
        <div className="flex flex-col gap-2 px-3">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton
              key={i}
              className="h-4"
              style={{ width: `${55 + ((i * 37) % 40)}%` }}
            />
          ))}
        </div>
      ) : showSteps && walkthrough ? (
        <StepList files={files} walkthrough={walkthrough} />
      ) : (
        <FileTree files={files} markers={markers} />
      )}
    </nav>
  )
}

function StepList({
  files,
  walkthrough,
}: {
  files: ReadonlyArray<ReviewDiffFile>
  walkthrough: NonNullable<Parameters<typeof buildEntries>[1]>
}) {
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const activeEntry = useReviewPage((state) => state.activeEntry)
  const steps = useMemo(() => {
    const byStep = new Map<number, Array<DiffEntry>>()
    for (const entry of buildEntries(files, walkthrough, "guide")) {
      if (!entry.step) continue
      byStep.set(entry.step.index, [
        ...(byStep.get(entry.step.index) ?? []),
        entry,
      ])
    }
    return [...byStep.values()]
  }, [files, walkthrough])
  return (
    <ol className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
      {steps.map((entries) => {
        const step = entries[0]!.step!
        const active = entries.some((entry) => entry.id === activeEntry)
        return (
          <li key={step.index}>
            <button
              type="button"
              aria-current={active ? "step" : undefined}
              onClick={() => jumpTo({ kind: "entry", id: entries[0]!.id })}
              className={cn(
                "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs leading-5 hover:bg-accent",
                active && "bg-accent"
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {step.index}
              </span>
              <span
                className={cn(
                  "min-w-0",
                  active ? "text-foreground" : "text-muted-foreground"
                )}
              >
                <InlineCode text={step.title} />
                <span className="block text-[11px] text-muted-foreground">
                  {entries.length} file{entries.length === 1 ? "" : "s"}
                </span>
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

function FileTree({
  files,
  markers,
}: {
  files: ReadonlyArray<ReviewDiffFile>
  markers: Markers
}) {
  const [filter, setFilter] = useState("")
  const tree = useMemo(() => {
    const query = filter.trim().toLowerCase()
    return buildTree(
      query
        ? files.filter((file) => file.path.toLowerCase().includes(query))
        : files
    )
  }, [files, filter])
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <label className="mx-3 mb-1.5 flex h-7 items-center gap-1.5 rounded-md border border-border bg-background px-2 text-xs focus-within:border-ring">
        <MagnifyingGlassIcon className="size-3.5 text-muted-foreground" />
        <input
          type="search"
          aria-label="Filter files"
          placeholder="Filter files"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground"
        />
      </label>
      <ul
        aria-label="Changed files"
        className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-4"
      >
        {tree.map((node) => (
          <TreeRow
            key={node.kind === "folder" ? node.path : node.file.path}
            node={node}
            depth={0}
            markers={markers}
          />
        ))}
      </ul>
    </div>
  )
}

const statusTint: Record<ReviewDiffFile["status"], string> = {
  added: "bg-success",
  removed: "bg-destructive",
  renamed: "bg-info",
  modified: "bg-warning",
}

const TreeRow = memo(function TreeRow({
  node,
  depth,
  markers,
}: {
  node: TreeNode
  depth: number
  markers: Markers
}) {
  const [open, setOpen] = useState(true)
  if (node.kind === "folder")
    return (
      <li>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          style={{ paddingLeft: 6 + depth * 12 }}
          className="flex h-6 w-full items-center gap-1 rounded-md pr-2 text-left text-xs text-muted-foreground hover:bg-accent"
        >
          <CaretDownIcon
            className={cn(
              "size-3 shrink-0 transition-transform",
              !open && "-rotate-90"
            )}
          />
          <FolderSimpleIcon className="size-3.5 shrink-0" />
          <span className="truncate">{node.name}</span>
        </button>
        {open && (
          <ul>
            {node.children.map((child) => (
              <TreeRow
                key={child.kind === "folder" ? child.path : child.file.path}
                node={child}
                depth={depth + 1}
                markers={markers}
              />
            ))}
          </ul>
        )}
      </li>
    )
  return (
    <FileRow
      file={node.file}
      name={node.name}
      depth={depth}
      markers={markers}
    />
  )
})

function FileRow({
  file,
  name,
  depth,
  markers,
}: {
  file: ReviewDiffFile
  name: string
  depth: number
  markers: Markers
}) {
  const active = useReviewPage((state) => state.activePath === file.path)
  const viewed = useReviewPage((state) => state.viewed.has(file.path))
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const ref = useRef<HTMLLIElement>(null)
  // Scroll only the file list; scrollIntoView would also move the page around it.
  useEffect(() => {
    const row = ref.current
    const list = row?.closest("ul[aria-label='Changed files']")
    if (!active || !row || !(list instanceof HTMLElement)) return
    const top = row.offsetTop - list.offsetTop
    if (top < list.scrollTop) list.scrollTop = top
    else if (top + row.offsetHeight > list.scrollTop + list.clientHeight)
      list.scrollTop = top + row.offsetHeight - list.clientHeight
  }, [active])
  const finding = markers.findings.get(file.path)
  const threads = markers.threads.get(file.path) ?? 0
  const description = [
    file.path,
    `${file.additions + file.deletions} lines changed`,
    finding && "Open SWE finding",
    threads > 0 && `${threads} open conversation${threads === 1 ? "" : "s"}`,
    viewed && "viewed",
  ]
    .filter(Boolean)
    .join(", ")
  return (
    <li ref={ref}>
      <button
        type="button"
        aria-current={active ? "location" : undefined}
        aria-label={description}
        onClick={() => jumpTo({ kind: "file", path: file.path })}
        title={file.path}
        style={{ paddingLeft: 22 + depth * 12 }}
        className={cn(
          "relative flex h-6 w-full items-center gap-1.5 rounded-md pr-2 text-left text-xs hover:bg-accent",
          active
            ? "bg-accent text-foreground"
            : viewed
              ? "text-muted-foreground"
              : "text-foreground/90"
        )}
      >
        {active && (
          <span className="absolute inset-y-1 left-0.5 w-0.5 rounded-full bg-primary" />
        )}
        <span
          className={cn(
            "size-1.5 shrink-0 rounded-full",
            statusTint[file.status]
          )}
        />
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            viewed && "line-through decoration-muted-foreground/40"
          )}
        >
          {name}
        </span>
        {finding && (
          <span
            className="size-1.5 shrink-0 rounded-full"
            style={{ background: findingGroupColor[finding] }}
            title="Open SWE finding"
          />
        )}
        {threads > 0 && (
          <span className="flex shrink-0 items-center gap-0.5 text-[10px] text-muted-foreground">
            <ChatCircleIcon className="size-3" />
            {threads}
          </span>
        )}
        {viewed ? (
          <CheckIcon weight="bold" className="size-3 shrink-0 text-primary" />
        ) : (
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground tabular-nums">
            {file.additions + file.deletions}
          </span>
        )}
      </button>
    </li>
  )
}
