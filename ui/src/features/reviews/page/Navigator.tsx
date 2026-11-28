import { Checkbox } from "@langchain/macaw-components/Checkbox"
import { Button } from "@langchain/macaw-components/Button"
import {
  CaretDownIcon,
  CaretRightIcon,
  CheckIcon,
  MagnifyingGlassRegularIcon,
} from "@langchain/macaw-components/icons"
import { Input } from "@langchain/macaw-components/Input"
import { Skeleton } from "@/components/Skeleton"
import { useQuery } from "@tanstack/react-query"
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { FolderSimpleIcon } from "@phosphor-icons/react/dist/ssr/FolderSimple"

import type { ReviewDiffFile, ReviewWalkthrough } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  buildEntries,
  compareTreePaths,
  filterEntries,
  groupSteps,
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
import { activeStep, isPassed, isRead, useReviewPage } from "./store"
import { StepBadge } from "./StepSection"
import { plural, splitPath } from "./text"
import { Icon } from "@langchain/macaw-components"

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

/** The walkthrough and file tree, with reading progress and findings. */
export function Navigator({ pr }: { pr: PullRequestRef }) {
  const files = useQuery(reviewQueries.diff(pr)).data?.files
  const walkthrough = useQuery(reviewQueries.detail(pr)).data?.walkthrough
  const markers = useFileMarkers(pr)
  const steps = walkthrough?.steps.length ?? 0
  const [walkthroughOpen, setWalkthroughOpen] = useState(true)
  const [filesOpen, setFilesOpen] = useState(true)

  return (
    <nav
      aria-label="Files & Walkthrough"
      className="flex h-full min-h-0 flex-col"
    >
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
      ) : (
        <>
          {walkthrough && steps > 0 ? (
            <NavigatorSection
              title="Walkthrough"
              count={steps}
              open={walkthroughOpen}
              onToggle={() => setWalkthroughOpen((open) => !open)}
              className={filesOpen ? "max-h-1/2" : "flex-1"}
            >
              <StepList files={files} walkthrough={walkthrough} />
            </NavigatorSection>
          ) : null}
          <NavigatorSection
            title="Files changed"
            count={files.length}
            open={filesOpen}
            onToggle={() => setFilesOpen((open) => !open)}
            className="flex-1"
          >
            <div className="px-space-3 pb-space-1">
              <FileFilter />
            </div>
            <FileTree files={files} markers={markers} />
          </NavigatorSection>
        </>
      )}
    </nav>
  )
}

function NavigatorSection({
  title,
  count,
  open,
  onToggle,
  className,
  children,
}: {
  title: string
  count: number
  open: boolean
  onToggle: () => void
  className: string
  children: ReactNode
}) {
  return (
    <section
      aria-label={title}
      className={cn(
        "flex min-h-0 flex-col border-t border-secondary",
        open ? className : "shrink-0"
      )}
    >
      <h3 aria-label={title} className="shrink-0">
        <Button
          color="secondary"
          variant="plain"
          size="xs"
          aria-label={`${title}, ${count}`}
          aria-expanded={open}
          onClick={onToggle}
          className="h-auto min-h-10 w-full justify-between border-0 px-space-4 py-space-2 font-medium text-tertiary focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset"
        >
          <span className="flex items-center gap-space-1">
            {title}
            <Icon icon={open ? CaretDownIcon : CaretRightIcon} aria-hidden />
          </span>
          <span className="tabular-nums">{count}</span>
        </Button>
      </h3>
      {open ? children : null}
    </section>
  )
}

function stepProgress(
  entries: ReadonlyArray<DiffEntry>,
  viewed: ReadonlySet<string>
): string {
  const files = new Set(entries.map((entry) => entry.file.path)).size
  const read = entries.filter((entry) => isRead(viewed, entry)).length
  if (read === entries.length) return "Read"
  if (read > 0) return `${read} of ${entries.length} parts read`
  return plural(files, "file")
}

function StepList({
  files,
  walkthrough,
}: {
  files: ReadonlyArray<ReviewDiffFile>
  walkthrough: ReviewWalkthrough
}) {
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const order = useReviewPage((state) => state.order)
  const markStepRead = useReviewPage((state) => state.markStepRead)
  const current = useReviewPage(activeStep)
  const viewed = useReviewPage((state) => state.viewed)
  const fileFilter = useReviewPage((state) => state.fileFilter)
  const guideEntries = useMemo(
    () => buildEntries(files, walkthrough, "guide"),
    [files, walkthrough]
  )
  // A filter keeps only the steps that touch a matching file, each with just those files.
  const groups = groupSteps(filterEntries(guideEntries, fileFilter))
  const passed = useReviewPage((state) =>
    groups
      .filter((group) => isPassed(state, group.step))
      .map((group) => group.step.index)
      .join(",")
  )
  if (groups.length === 0)
    return (
      <p className="px-space-4 py-space-2 text-xs text-secondary">
        No step touches a matching file.
      </p>
    )
  return (
    <ol className="scroll-mask-t scroll-mask-b min-h-0 flex-1 overflow-y-auto px-space-2 pb-space-3">
      {groups.map(({ step, entries }) => {
        const active = step.index === current
        const read = entries.every((entry) => isRead(viewed, entry))
        // Scrolled past without being marked: the toggle stays faintly in view as an offer.
        const offered = !read && passed.split(",").includes(`${step.index}`)
        const additions = entries.reduce(
          (sum, entry) => sum + entry.additions,
          0
        )
        const deletions = entries.reduce(
          (sum, entry) => sum + entry.deletions,
          0
        )
        const progress = stepProgress(entries, viewed)
        return (
          <li
            key={step.index}
            onPointerLeave={(event) => {
              if (event.pointerType !== "mouse") return
              event.currentTarget
                .querySelector<HTMLButtonElement>(
                  "[data-step-read-toggle]:focus:not(:focus-visible)"
                )
                ?.blur()
            }}
            className={cn(
              "group/step relative flex items-start rounded-md",
              active
                ? "bg-selected hover:bg-selected-hover"
                : "hover:bg-surface-level-1-hover"
            )}
          >
            <Button
              color="secondary"
              variant="plain"
              size="sm"
              aria-label={`Step ${step.index}: ${step.title}, ${progress}, ${plural(additions, "addition")}, ${plural(deletions, "deletion")}`}
              type="button"
              aria-current={active ? "step" : undefined}
              onClick={() =>
                // Stacked beside a diff in file order, guide entry ids aren't in the diff.
                jumpTo(
                  order === "guide"
                    ? { kind: "entry", id: entries[0]!.id }
                    : { kind: "file", path: entries[0]!.file.path }
                )
              }
              className="h-auto min-w-0 flex-1 items-start justify-start gap-space-2 border-0 px-space-2 pt-space-2 pb-1.5 text-left leading-5 whitespace-normal hover:bg-transparent focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none dark:hover:bg-transparent"
            >
              <StepBadge
                index={step.index}
                read={read}
                active={active}
                className={cn(
                  "size-4 group-hover/step:opacity-0 group-has-[[data-step-read-toggle]:is(:focus,:active)]/step:opacity-0 motion-safe:transition-opacity",
                  read && "rounded-[3px]",
                  offered && "opacity-0"
                )}
              />
              <span
                className={cn(
                  "min-w-0",
                  active ? "text-primary" : "text-secondary",
                  read && !active && "text-tertiary"
                )}
              >
                <InlineCode text={step.title} className="leading-none" />
                <span className="mt-space-1 flex flex-wrap items-baseline gap-x-space-2 text-xs text-quaternary tabular-nums">
                  {progress}
                  <span className="font-mono">
                    {additions > 0 && (
                      <span className="text-success-secondary">
                        +{additions}
                      </span>
                    )}{" "}
                    {deletions > 0 && (
                      <span className="text-error-secondary">−{deletions}</span>
                    )}
                  </span>
                </span>
              </span>
            </Button>
            <div className="absolute top-2 left-2">
              <Checkbox
                checked={read}
                data-step-read-toggle
                aria-label={`Mark step ${step.index} read`}
                title={read ? "Mark step unread" : "Mark step read"}
                onCheckedChange={() => markStepRead(step.index, guideEntries)}
                size="sm"
                containerClassName="peer/step-read"
                className={cn(
                  "focus:opacity-100 active:opacity-100 motion-safe:transition-opacity",
                  offered
                    ? "border-brand opacity-70 hover:opacity-100"
                    : "opacity-0 group-hover/step:opacity-100"
                )}
              />
              {/* Radix draws no check while unchecked; the faint one suggests marking this step read. */}
              {offered && !read && (
                <CheckIcon
                  aria-hidden="true"
                  weight="bold"
                  className="pointer-events-none absolute inset-0 m-auto size-3 text-brand-primary opacity-70 peer-focus-within/step-read:opacity-100 peer-hover/step-read:opacity-100"
                />
              )}
            </div>
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
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={node.name}
        title={node.path}
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={{ paddingLeft: 6 + depth * 12 }}
        className="h-6 w-full justify-start gap-space-1 pr-space-2 text-left whitespace-normal text-secondary hover:bg-surface-level-1-hover focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
      >
        <CaretDownIcon
          className={cn(
            "size-3 shrink-0 transition-transform",
            !open && "-rotate-90"
          )}
        />
        <FolderSimpleIcon className="size-3.5 shrink-0" weight="regular" />
        <span className="truncate">{node.name}</span>
      </Button>
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
    worst && "Robin finding",
    threads > 0 && plural(threads, "open conversation"),
    viewed && "viewed",
  ]
    .filter(Boolean)
    .join(", ")
  return (
    <li ref={ref}>
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        type="button"
        aria-current={active ? "location" : undefined}
        aria-label={description}
        onClick={() =>
          jumpTo({ kind: "file", path: file.path }, { reveal: true })
        }
        title={file.path}
        style={{ paddingLeft: 22 + depth * 12 }}
        className={cn(
          "focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
          "justify-start",
          "relative h-6 w-full gap-space-2 pr-space-2 text-left hover:bg-surface-level-1-hover",
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
            title="Robin finding"
          />
        )}
        {threads > 0 && (
          <span className="flex shrink-0 items-center gap-0.5 text-xxs text-secondary">
            <ChatCircleIcon className="size-3" weight="regular" />
            {threads}
          </span>
        )}
        <span
          className="flex shrink-0 gap-space-1 font-mono text-xxs tabular-nums"
          title={`${file.additions} lines added, ${file.deletions} lines deleted`}
        >
          <span className="text-success-secondary">+{file.additions}</span>
          <span className="text-error-secondary">−{file.deletions}</span>
        </span>
        {viewed && (
          <CheckIcon
            weight="bold"
            className="size-3 shrink-0 text-brand-primary"
          />
        )}
      </Button>
    </li>
  )
}
