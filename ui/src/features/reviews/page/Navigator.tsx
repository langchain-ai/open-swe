import {
  CaretDownIcon,
  CheckIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import { Input } from "@langchain/macaw-components/Input"
import { ProgressBar } from "@langchain/macaw-components/ProgressBar"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useQuery } from "@tanstack/react-query"
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { FolderSimpleIcon } from "@phosphor-icons/react/dist/ssr/FolderSimple"

import type { ReviewDiffFile, ReviewWalkthrough } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  buildEntries,
  compareTreePaths,
  filterEntries,
  matchesFileFilter,
  type DiffEntry,
} from "./diffEntries"
import { findingGroupColor } from "./findings"
import { InlineCode } from "./inlineCode"
import {
  reviewQueries,
  useFileMarkers,
  type FileMarkers,
  type PullRequestRef,
} from "./queries"
import { ReadingOrderTabs } from "./ReadingOrderTabs"
import { useReviewPage } from "./store"
import { plural, splitPath } from "./text"

interface FolderNode {
  kind: "folder"
  name: string
  path: string
  children: Array<TreeNode>
}

interface FileNode {
  kind: "file"
  name: string
  path: string
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
      name: splitPath(file.path).name,
      path: file.path,
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

/** The left rail: every file with what's been read and what needs attention, or the walkthrough's steps. */
export function Navigator({ pr }: { pr: PullRequestRef }) {
  const files = useQuery(reviewQueries.diff(pr)).data?.files
  const walkthrough = useQuery(reviewQueries.detail(pr)).data?.walkthrough
  const order = useReviewPage((state) => state.order)
  const viewedCount = useReviewPage(
    (state) => files?.filter((file) => state.viewed.has(file.path)).length ?? 0
  )
  const markers = useFileMarkers(pr)
  const steps = walkthrough?.steps.length ?? 0

  return (
    <nav aria-label="Files" className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-space-2 px-space-3 pt-space-3 pb-space-2">
        {steps > 0 && (
          <ReadingOrderTabs
            steps={steps}
            files={files?.length}
            className="w-full [&>*]:flex-1"
          />
        )}
        {files && (
          <>
            <div className="flex items-center gap-space-2 text-xxs text-secondary tabular-nums">
              <ProgressBar
                aria-label="Files viewed"
                size="sm"
                value={viewedCount}
                max={Math.max(files.length, 1)}
                className="flex-1"
              />
              {viewedCount}/{files.length} viewed
            </div>
            <FileFilter />
          </>
        )}
      </div>
      {!files ? (
        <div className="flex flex-col gap-space-2 px-space-3">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton
              key={i}
              className="h-4"
              style={{ width: `${55 + ((i * 37) % 40)}%` }}
            />
          ))}
        </div>
      ) : order === "guide" && walkthrough && steps > 0 ? (
        <StepList files={files} walkthrough={walkthrough} />
      ) : (
        <FileTree files={files} markers={markers} />
      )}
    </nav>
  )
}

interface StepGroup {
  step: NonNullable<DiffEntry["step"]>
  entries: Array<DiffEntry>
}

function stepProgress(
  entries: ReadonlyArray<DiffEntry>,
  viewed: ReadonlySet<string>
): string {
  const read = entries.filter((entry) => viewed.has(entry.file.path)).length
  if (read === entries.length) return `All ${entries.length} viewed`
  if (read > 0) return `${read} of ${entries.length} viewed`
  return plural(entries.length, "file")
}

function StepList({
  files,
  walkthrough,
}: {
  files: ReadonlyArray<ReviewDiffFile>
  walkthrough: ReviewWalkthrough
}) {
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const activeEntry = useReviewPage((state) => state.activeEntry)
  const viewed = useReviewPage((state) => state.viewed)
  const fileFilter = useReviewPage((state) => state.fileFilter)
  // A filter keeps only the steps that touch a matching file, each with just those files.
  const groups = useMemo(() => {
    const byStep = new Map<number, StepGroup>()
    for (const entry of filterEntries(
      buildEntries(files, walkthrough, "guide"),
      fileFilter
    )) {
      if (!entry.step) continue
      const group = byStep.get(entry.step.index) ?? {
        step: entry.step,
        entries: [],
      }
      group.entries.push(entry)
      byStep.set(entry.step.index, group)
    }
    return [...byStep.values()]
  }, [files, walkthrough, fileFilter])
  if (groups.length === 0)
    return (
      <p className="px-space-4 py-space-2 text-xs text-secondary">
        No step touches a matching file.
      </p>
    )
  return (
    <ol className="min-h-0 flex-1 overflow-y-auto px-space-2 pb-space-4">
      {groups.map(({ step, entries }) => {
        const active = entries.some((entry) => entry.id === activeEntry)
        return (
          <li key={step.index}>
            <button
              type="button"
              aria-current={active ? "step" : undefined}
              onClick={() => jumpTo({ kind: "entry", id: entries[0]!.id })}
              className={cn(
                "flex w-full items-start gap-space-2 rounded-md px-space-2 py-space-1 text-left text-xs leading-5 hover:bg-surface-level-1-hover",
                active && "bg-selected"
              )}
            >
              <span
                className={cn(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-xxs font-semibold tabular-nums",
                  active
                    ? "bg-brand text-brand-on-fill"
                    : "bg-surface-level-3 text-secondary"
                )}
              >
                {step.index}
              </span>
              <span
                className={cn(
                  "min-w-0",
                  active ? "text-primary" : "text-secondary"
                )}
              >
                <InlineCode text={step.title} />
                <span className="block text-xxs text-secondary tabular-nums">
                  {stepProgress(entries, viewed)}
                </span>
              </span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

/** One filter for the tree, the walkthrough and the diff itself. */
function FileFilter() {
  const filter = useReviewPage((state) => state.fileFilter)
  const setFilter = useReviewPage((state) => state.setFileFilter)
  return (
    <Input
      size="sm"
      leftIcon={MagnifyingGlassRegularIcon}
      aria-label="Filter files"
      placeholder="Filter files"
      debounceMs={0}
      value={filter}
      onChange={setFilter}
      onKeyDown={(event) => {
        if (event.key === "Escape" && filter) {
          event.stopPropagation()
          setFilter("")
        }
      }}
    />
  )
}

function FileTree({
  files,
  markers,
}: {
  files: ReadonlyArray<ReviewDiffFile>
  markers: ReadonlyMap<string, FileMarkers>
}) {
  const filter = useReviewPage((state) => state.fileFilter)
  const list = useRef<HTMLUListElement>(null)
  // Scroll only the file list; scrollIntoView would also move the page around it.
  const reveal = useCallback((row: HTMLElement) => {
    const scroller = list.current
    if (!scroller) return
    const top = row.offsetTop - scroller.offsetTop
    if (top < scroller.scrollTop) scroller.scrollTop = top
    else if (
      top + row.offsetHeight >
      scroller.scrollTop + scroller.clientHeight
    )
      scroller.scrollTop = top + row.offsetHeight - scroller.clientHeight
  }, [])
  const tree = useMemo(
    () =>
      buildTree(files.filter((file) => matchesFileFilter(file.path, filter))),
    [files, filter]
  )
  return (
    <ul
      ref={list}
      aria-label="Changed files"
      className="min-h-0 flex-1 overflow-y-auto px-space-1 pb-space-4"
    >
      {tree.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          depth={0}
          markers={markers}
          reveal={reveal}
        />
      ))}
    </ul>
  )
}

const statusTint: Record<ReviewDiffFile["status"], string> = {
  added: "bg-success-strong",
  removed: "bg-error-strong",
  renamed: "bg-brand",
  modified: "bg-warning-strong",
}

const TreeRow = memo(function TreeRow({
  node,
  depth,
  markers,
  reveal,
}: {
  node: TreeNode
  depth: number
  markers: ReadonlyMap<string, FileMarkers>
  reveal: (row: HTMLElement) => void
}) {
  const [open, setOpen] = useState(true)
  if (node.kind === "file")
    return (
      <FileRow
        file={node.file}
        name={node.name}
        depth={depth}
        markers={markers.get(node.path)}
        reveal={reveal}
      />
    )
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={{ paddingLeft: 6 + depth * 12 }}
        className="flex h-6 w-full items-center gap-space-1 rounded-md pr-space-2 text-left text-xs text-secondary hover:bg-surface-level-1-hover"
      >
        <CaretDownIcon
          className={cn(
            "size-3 shrink-0 transition-transform",
            !open && "-rotate-90"
          )}
        />
        <FolderSimpleIcon className="size-3.5 shrink-0" weight="regular" />
        <span className="truncate">{node.name}</span>
      </button>
      {open && (
        <ul>
          {node.children.map((child) => (
            <TreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              markers={markers}
              reveal={reveal}
            />
          ))}
        </ul>
      )}
    </li>
  )
})

function FileRow({
  file,
  name,
  depth,
  markers,
  reveal,
}: {
  file: ReviewDiffFile
  name: string
  depth: number
  markers: FileMarkers | undefined
  reveal: (row: HTMLElement) => void
}) {
  const active = useReviewPage((state) => state.activePath === file.path)
  const viewed = useReviewPage((state) => state.viewed.has(file.path))
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (active && ref.current) reveal(ref.current)
  }, [active, reveal])
  const worst = markers?.findings[0]
  const threads = markers?.threads.length ?? 0
  const changed = file.additions + file.deletions
  const description = [
    file.path,
    `${changed} lines changed`,
    worst && "Open SWE finding",
    threads > 0 && plural(threads, "open conversation"),
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
          "relative flex h-6 w-full items-center gap-space-2 rounded-md pr-space-2 text-left text-xs hover:bg-surface-level-1-hover",
          active
            ? "bg-selected text-primary"
            : viewed
              ? "text-secondary"
              : "text-primary"
        )}
      >
        {active && (
          <span className="absolute inset-y-1 left-0.5 w-0.5 rounded-full bg-brand" />
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
            viewed && "line-through decoration-text-tertiary"
          )}
        >
          {name}
        </span>
        {worst && (
          <span
            className="size-1.5 shrink-0 rounded-full"
            style={{ background: findingGroupColor[worst.group] }}
            title="Open SWE finding"
          />
        )}
        {threads > 0 && (
          <span className="flex shrink-0 items-center gap-0.5 text-xxs text-secondary">
            <ChatCircleIcon className="size-3" weight="regular" />
            {threads}
          </span>
        )}
        {viewed ? (
          <CheckIcon
            weight="bold"
            className="size-3 shrink-0 text-brand-primary"
          />
        ) : (
          <span className="shrink-0 font-mono text-xxs text-secondary tabular-nums">
            {changed}
          </span>
        )}
      </button>
    </li>
  )
}
