import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ArrowSquareOutIcon } from "@phosphor-icons/react"
import {
  Virtualizer,
  WorkerPoolContextProvider,
  useVirtualizer,
} from "@pierre/diffs/react"

import type {
  PrReviewComment,
  ReviewDetail,
  ReviewDiffFile,
  ReviewFinding,
} from "@/features/reviews/lib/api"
import type {
  ReviewSidebarGroup,
  ReviewSidebarView,
} from "@/features/reviews/components/ReviewSidebar"
import type { ReviewAnnotation } from "@/features/reviews/components/FileDiffCard"
import type { SideTab } from "@/features/reviews/components/SidePanel"
import type { DiffVirtualizer } from "@/features/reviews/lib/diffScroll"
import type { ExpandedFindingContextValue } from "@/features/reviews/lib/findings"
import { Markdown } from "@/components/markdown/Markdown"
import { DiffWrapToggle } from "@/components/diff/DiffWrapToggle"
import { PrHeader } from "@/features/reviews/components/PrHeader"
import {
  ReviewChatComposerProvider,
  useReviewChatComposer,
} from "@/features/reviews/components/ReviewChat"
import { ReviewSidebarPanel } from "@/features/reviews/components/ReviewSidebar"
import { DiffStyleToggle } from "@/features/reviews/components/DiffStyleToggle"
import {
  FileDiffCard,
  GroupHeader,
} from "@/features/reviews/components/FileDiffCard"
import { SidePanel } from "@/features/reviews/components/SidePanel"
import {
  DIFF_VIRTUALIZER_CONFIG,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  warmDiffHighlighter,
} from "@/components/diff/diffUtils"
import { Skeleton } from "@/components/ui/skeleton"
import { reviewImageProxyUrl } from "@/features/reviews/lib/api"
import { resolveDiffGroups } from "@/features/reviews/lib/diffGroups"
import {
  ExpandedFindingContext,
  findingSelectedRange,
  isAnchored,
} from "@/features/reviews/lib/findings"
import {
  useDiffStylePref,
  useReadFindings,
  useReviewViewPref,
  useViewedFiles,
} from "@/features/reviews/lib/reviewPrefs"
import { useDiffNavigation } from "@/features/reviews/lib/useDiffNavigation"
import { useReviewSelection } from "@/features/reviews/lib/useReviewSelection"
import { cn } from "@/lib/utils"

const NO_FINDINGS: Array<ReviewFinding> = []

export type ReviewMainBodyVariant = "full" | "embedded"

export interface ReviewMainBodyProps {
  detail: ReviewDetail
  diffFiles: Array<ReviewDiffFile> | null
  // "full" renders the side panel + chat alongside the diffs; "embedded" renders
  // just the main body with an expand affordance (used inside the git panel).
  variant?: ReviewMainBodyVariant
  onExpand?: () => void
  // A PR comment opened from the comments dropdown: shown inline at its line.
  openComment?: PrReviewComment | null
  onUpdateOpenComment?: (comment: PrReviewComment) => void
  onCloseOpenComment?: () => void
}

export function ReviewMainBody({
  detail,
  diffFiles,
  variant = "full",
  onExpand,
  openComment,
  onUpdateOpenComment,
  onCloseOpenComment,
}: ReviewMainBodyProps) {
  // The composer provider lives here so it remounts in lockstep with the
  // head_sha-keyed body (and the activeId-keyed chat thread). The embedded
  // variant has no chat, so it skips the provider.
  if (variant === "embedded") {
    return (
      <ReviewBodyInner
        detail={detail}
        diffFiles={diffFiles}
        variant="embedded"
        onExpand={onExpand}
      />
    )
  }
  return (
    <ReviewChatComposerProvider>
      <ReviewBodyInner
        detail={detail}
        diffFiles={diffFiles}
        variant="full"
        openComment={openComment ?? null}
        onUpdateOpenComment={onUpdateOpenComment}
        onCloseOpenComment={onCloseOpenComment}
      />
    </ReviewChatComposerProvider>
  )
}

function ReviewBodyInner({
  detail,
  diffFiles,
  variant,
  onExpand,
  openComment = null,
  onUpdateOpenComment,
  onCloseOpenComment,
}: {
  detail: ReviewDetail
  diffFiles: Array<ReviewDiffFile> | null
  variant: ReviewMainBodyVariant
  onExpand?: () => void
  openComment?: PrReviewComment | null
  onUpdateOpenComment?: (comment: PrReviewComment) => void
  onCloseOpenComment?: () => void
}) {
  const embedded = variant === "embedded"
  const composer = useReviewChatComposer()
  const transformPrImage = useCallback(
    (src: string) =>
      reviewImageProxyUrl(detail.owner, detail.repo, detail.number, src),
    [detail.owner, detail.repo, detail.number]
  )
  const [sideTab, setSideTab] = useState<SideTab>("info")
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [diffStyle, setDiffStyle] = useDiffStylePref()

  useEffect(() => {
    void warmDiffHighlighter()
  }, [])

  const expandedFinding = useMemo(
    () => detail.findings.find((f) => f.id === expandedId) ?? null,
    [detail.findings, expandedId]
  )
  // Latest-value ref so the callbacks below stay referentially stable while
  // still reading the current expansion.
  const expandedFindingRef = useRef(expandedFinding)
  expandedFindingRef.current = expandedFinding
  const collapseFinding = useCallback(() => setExpandedId(null), [])

  const { viewed, setFileViewed } = useViewedFiles(
    detail.owner,
    detail.repo,
    detail.number,
    detail.head_sha
  )
  const { read, markRead, markAllRead } = useReadFindings(detail.thread_id)
  const markAllFindingsRead = useCallback(
    () => markAllRead(detail.findings.map((f) => f.id)),
    [detail.findings, markAllRead]
  )

  const findingsByFile = useMemo(() => {
    const byFile = new Map<string, Array<ReviewFinding>>()
    for (const finding of detail.findings) {
      if (!isAnchored(finding)) continue
      const list = byFile.get(finding.file) ?? []
      list.push(finding)
      byFile.set(finding.file, list)
    }
    return byFile
  }, [detail.findings])

  const linesLeft = useMemo(() => {
    if (!diffFiles) return null
    return diffFiles
      .filter((file) => !viewed.has(file.path))
      .reduce((acc, file) => acc + file.additions + file.deletions, 0)
  }, [diffFiles, viewed])

  const groupedView = useMemo(
    () =>
      resolveDiffGroups(
        diffFiles,
        detail.diff_groups,
        detail.diff_groups_stale
      ),
    [diffFiles, detail.diff_groups, detail.diff_groups_stale]
  )

  const sidebarGroups = useMemo<Array<ReviewSidebarGroup> | null>(() => {
    if (!groupedView) return null
    return groupedView.map((group) => ({
      index: group.index,
      title: group.title,
    }))
  }, [groupedView])

  // The view follows fresh-group availability until the user explicitly picks
  // one, after which the choice persists across PRs.
  const hasFreshGroups =
    detail.diff_groups.length > 0 && !detail.diff_groups_stale
  const [explicitView, setView] = useReviewViewPref()
  const view: ReviewSidebarView =
    explicitView ?? (hasFreshGroups ? "ai" : "files")

  const filesByPath = useMemo(
    () => new Map((diffFiles ?? []).map((file) => [file.path, file])),
    [diffFiles]
  )

  const onFileHidden = useCallback((path: string) => {
    if (expandedFindingRef.current?.file === path) setExpandedId(null)
  }, [])

  const nav = useDiffNavigation<ReviewAnnotation>({
    groups: view === "ai" ? groupedView : null,
    viewed,
    setFileViewed,
    filesByPath,
    onFileHidden,
  })

  const focusChat = useCallback(() => setSideTab("chat"), [])
  const selection = useReviewSelection({
    filesByPath,
    composer,
    onDiffFocus: collapseFinding,
    onAddedToChat: focusChat,
  })

  // Toggle a finding from its in-diff header — it's already on-screen, so no
  // scrolling is needed.
  const toggleInline = useCallback(
    (finding: ReviewFinding) => {
      markRead(finding.id)
      setExpandedId((prev) => (prev === finding.id ? null : finding.id))
    },
    [markRead]
  )

  // Open a finding from the side panel. Anchored findings expand inline in the
  // diff and are scrolled into view; non-anchored ones expand inline in the panel.
  const { clearSelection } = selection
  const { revealFinding, revealComment } = nav
  const openFromPanel = useCallback(
    (finding: ReviewFinding) => {
      markRead(finding.id)
      const willExpand = expandedFindingRef.current?.id !== finding.id
      clearSelection()
      setExpandedId(willExpand ? finding.id : null)
      revealFinding(willExpand ? finding : null)
    },
    [markRead, clearSelection, revealFinding]
  )

  // Comments whose file/line aren't in the current diff (e.g. outdated) have no
  // inline anchor, so fall back to GitHub.
  const closeOpenCommentRef = useRef(onCloseOpenComment)
  closeOpenCommentRef.current = onCloseOpenComment
  useEffect(() => {
    if (!openComment) return
    revealComment(openComment, () => {
      if (openComment.html_url) {
        window.open(openComment.html_url, "_blank", "noopener,noreferrer")
      }
      closeOpenCommentRef.current?.()
    })
  }, [openComment, revealComment])

  useEffect(() => {
    if (!expandedId) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpandedId(null)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [expandedId])

  const renderFileCard = (file: ReviewDiffFile) => {
    // Keep the range highlighted while its comment composer is open, so the
    // user can see exactly which lines they're commenting on.
    const { userSelection, commentDraft } = selection
    const selectedLines =
      expandedFinding?.file === file.path && isAnchored(expandedFinding)
        ? findingSelectedRange(expandedFinding)
        : commentDraft?.file === file.path
          ? commentDraft.range
          : userSelection?.file === file.path
            ? userSelection.range
            : null
    return (
      <FileDiffCard
        key={file.path}
        file={file}
        findings={findingsByFile.get(file.path) ?? NO_FINDINGS}
        selectedLines={selectedLines}
        viewed={viewed.has(file.path)}
        onToggleViewed={nav.toggleFileViewed}
        expanded={nav.isFileExpanded(file.path)}
        onToggleExpanded={nav.toggleFileExpanded}
        onSelectLines={selection.selectLines}
        onAddToChat={embedded ? undefined : selection.addToChat}
        registerSection={nav.registerSection}
        registerDiffInstance={nav.registerDiffInstance}
        diffStyle={diffStyle}
        owner={detail.owner}
        repo={detail.repo}
        prNumber={detail.number}
        commentDraftRange={
          commentDraft?.file === file.path ? commentDraft.range : null
        }
        onStartComment={embedded ? undefined : selection.startComment}
        onCloseComment={selection.closeComment}
        openComment={openComment?.path === file.path ? openComment : null}
        onUpdateOpenComment={onUpdateOpenComment}
        onCloseOpenComment={onCloseOpenComment}
      />
    )
  }

  const sidebarData = useMemo(
    () => ({
      title: `PR #${detail.number}`,
      files: diffFiles,
      selected: nav.selectedFile,
      viewed,
      onSelect: nav.scrollToFile,
      groups: sidebarGroups,
      view,
      onViewChange: setView,
      onSelectGroup: nav.scrollToGroup,
      activeGroup: nav.activeGroup,
    }),
    [
      detail.number,
      diffFiles,
      nav.selectedFile,
      viewed,
      nav.scrollToFile,
      sidebarGroups,
      view,
      setView,
      nav.scrollToGroup,
      nav.activeGroup,
    ]
  )

  const expandedFindingCtx = useMemo<ExpandedFindingContextValue>(
    () => ({
      expandedId,
      reviewUrl: detail.url,
      toggle: toggleInline,
      registerAnnotation: nav.registerAnnotation,
    }),
    [expandedId, detail.url, toggleInline, nav.registerAnnotation]
  )

  return (
    <ExpandedFindingContext.Provider value={expandedFindingCtx}>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <main className="relative flex min-h-0 min-w-0 flex-1">
          {!embedded && (
            <div className="hidden w-72 shrink-0 flex-col border-r border-border bg-sidebar lg:flex">
              <ReviewSidebarPanel data={sidebarData} />
            </div>
          )}
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            {embedded && (
              <div className="flex h-9 shrink-0 items-center justify-end border-b border-border px-3">
                <button
                  type="button"
                  onClick={onExpand}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                >
                  <ArrowSquareOutIcon className="size-3" />
                  Open full review
                </button>
              </div>
            )}
            <WorkerPoolContextProvider
              poolOptions={DIFF_WORKER_POOL_OPTIONS}
              highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
            >
              <Virtualizer
                className="relative min-h-0 flex-1 overflow-y-auto"
                contentClassName={cn(
                  "mx-auto w-full px-6 py-6",
                  diffStyle === "split" ? "max-w-none" : "max-w-6xl"
                )}
                config={DIFF_VIRTUALIZER_CONFIG}
              >
                <VirtualizerBridge
                  probeRef={nav.scrollerProbe}
                  instanceRef={nav.virtualizerRef}
                />
                <PrHeader
                  url={detail.url}
                  title={detail.pr.title}
                  state={detail.pr.state}
                  headRef={detail.pr.head_ref}
                  baseRef={detail.pr.base_ref}
                  author={detail.pr.author?.login}
                  stats={{
                    changedFiles: detail.pr.changed_files,
                    additions: detail.pr.additions,
                    deletions: detail.pr.deletions,
                  }}
                />
                <div className="mt-4 rounded-lg border border-border bg-card p-4">
                  {detail.pr.body ? (
                    <Markdown
                      content={detail.pr.body}
                      transformImageUrl={transformPrImage}
                    />
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      This PR has no description.
                    </p>
                  )}
                </div>

                <div className="mt-6">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <h2 className="text-sm font-medium">Changes</h2>
                    <div className="flex items-center gap-3">
                      {linesLeft !== null && (
                        <span className="text-xs text-muted-foreground">
                          {linesLeft === 0
                            ? "All lines reviewed"
                            : `${linesLeft} lines left`}
                        </span>
                      )}
                      {diffFiles && diffFiles.length > 0 && (
                        <div className="flex items-center gap-1">
                          <DiffWrapToggle className="size-5" />
                          <DiffStyleToggle
                            value={diffStyle}
                            onChange={setDiffStyle}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                  {!diffFiles ? (
                    <Skeleton className="h-64 w-full" />
                  ) : diffFiles.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No diff available.
                    </p>
                  ) : view === "ai" && groupedView ? (
                    <div className="space-y-6">
                      {groupedView.map((group) => (
                        <div
                          key={group.index}
                          ref={(node) => nav.registerGroup(group.index, node)}
                          className="scroll-mt-4 space-y-3"
                        >
                          <GroupHeader group={group} />
                          {group.files.map(renderFileCard)}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {diffFiles.map(renderFileCard)}
                    </div>
                  )}
                </div>
              </Virtualizer>
            </WorkerPoolContextProvider>
          </div>
        </main>

        {!embedded && (
          <SidePanel
            detail={detail}
            tab={sideTab}
            onTabChange={setSideTab}
            read={read}
            expandedId={expandedId}
            onMarkAllRead={markAllFindingsRead}
            onFindingClick={openFromPanel}
          />
        )}
      </div>
    </ExpandedFindingContext.Provider>
  )
}

// Grabs the virtualizer instance from context (only available inside
// <Virtualizer>) and lifts it to the parent ref so scroll-to can read accurate
// offsets. Doubles as the hidden scroll-element probe.
function VirtualizerBridge({
  probeRef,
  instanceRef,
}: {
  probeRef: (node: HTMLDivElement | null) => void
  instanceRef: React.RefObject<DiffVirtualizer | null>
}) {
  const virtualizer = useVirtualizer()
  useEffect(() => {
    instanceRef.current = virtualizer ?? null
  }, [virtualizer, instanceRef])
  return <div ref={probeRef} aria-hidden className="hidden" />
}
