import { useEffect, useLayoutEffect, useRef } from "react"
import { create } from "zustand"
import { toast } from "sonner"
import type { SelectedLineRange } from "@pierre/diffs"

import type { PullRequestReviewEvent } from "@/lib/api"
import type { DiffStyle } from "@/features/agents/utils/diffUtils"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"
import type { DiffSide } from "@/features/reviews/lib/chatDiffActions"
import { rangeBounds } from "@/features/reviews/lib/lineRange"
import {
  readPreference,
  writePreference,
} from "@/features/reviews/lib/preferences"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"
import { findEntry, isRenderable, type DiffEntry } from "./diffEntries"

export type RailTab = "chat" | "discussion"
export type DiffOrder = "files" | "guide"

export type DiffTarget =
  | { kind: "top" }
  | { kind: "file"; path: string }
  | { kind: "entry"; id: string }
  | {
      kind: "line"
      path: string
      line: number
      side: DiffSide
      /** First line of a multi-line target; the whole range is highlighted. */
      start?: number
    }

export type LineTarget = Extract<DiffTarget, { kind: "line" }>

export interface CommentDraftTarget {
  path: string
  range: SelectedLineRange
}

/** Things a click asks another part of the page to do once, whenever that part is next on screen. */
export type PageEvent = "showFindings" | "showOpenConversations"

/** The viewer's settings; they carry across pull requests, and some across reloads. */
interface Preferences {
  /** Whether the file list sits beside the diff, where there is room for it. */
  navigatorOpen: boolean
  order: DiffOrder
  diffStyle: DiffStyle
}

/** What the viewer marked on one head of the pull request; a push starts it over. */
interface HeadState {
  /** The head the viewed marks belong to. */
  viewedSha: string | null
  viewed: ReadonlySet<string>
  /** Read keys whose collapsed state the viewer flipped away from the default (read = collapsed). */
  collapsed: ReadonlySet<string>
}

/** Words the viewer started writing on this pull request, kept until they open another one. */
interface PullRequestState {
  pr: PullRequestRef | null
  composer: CommentDraftTarget | null
  /** What's typed in the composer; kept here so a half-written comment is never moved or lost. */
  composerText: string
  /** Replies being written, by thread id; a thread with an entry has its reply box open. */
  replyDrafts: ReadonlyMap<number, string>
  /** Pending review comments being edited, by comment id, with the edited text. */
  pendingEdits: ReadonlyMap<number, string>
  /** Code attached to the next chat message, shown as chips above the composer. */
  chatExcerpts: ReadonlyArray<CodeExcerpt>
}

/** One visit to the page: where the reader is and what's open. Leaving the page drops it. */
interface VisitState {
  railTab: RailTab
  /** Below the wide breakpoint the rail is an overlay. */
  railOpen: boolean
  /** The file list over the page, where there is no room beside the diff. */
  navigatorOverlay: boolean
  reviewOpen: boolean
  reviewVerdict: PullRequestReviewEvent
  /** The file, and its diff entry, under the top of the diff viewport. */
  activePath: string | null
  activeEntry: string | null
  /** Walkthrough steps the reader scrolled into, so passing one can suggest marking it read. */
  visitedSteps: ReadonlySet<number>
  /** Whether the end of the diff is on screen, which passes the last step. */
  atEnd: boolean
  /** Bumped per request so asking for the same target twice still scrolls. */
  jump: { key: number; target: DiffTarget } | null
  expandedFinding: string | null
  /** Threads on the diff the reader opened or folded, against their default. */
  threadsOpen: ReadonlyMap<number, boolean>
  chatDraft: { key: number; text: string } | undefined
  /** A question the chat sends as soon as it can, then clears. */
  chatQuestion: string | undefined
  /** Events not yet taken by the part of the page they're for. */
  events: ReadonlySet<PageEvent>
  entryOrder: ReadonlyArray<DiffEntry>
  /** Narrows the diff, the file tree and the walkthrough to matching paths. */
  fileFilter: string
  /** Whether the status card is on screen; when it isn't, the header carries its sentence. */
  standingInView: boolean
  /** The finding or thread n/p last landed on, so they move on from it even after the list changes. */
  lastStop: string | null
}

type ReviewPageState = Preferences & HeadState & PullRequestState & VisitState

interface ReviewPageActions {
  /** Shows a pull request at a head; drafts survive only on the same pull request, marks only on the same head. */
  open: (pr: PullRequestRef, headSha: string | null) => void
  /** Drops what only this visit needs, keeping drafts and marks for coming back. */
  leave: () => void
  setRailTab: (tab: RailTab) => void
  setRailOpen: (open: boolean) => void
  toggleNavigator: () => void
  setNavigatorOverlay: (open: boolean) => void
  openReview: (verdict?: PullRequestReviewEvent) => void
  setReviewOpen: (open: boolean) => void
  setOrder: (order: DiffOrder) => void
  setDiffStyle: (style: DiffStyle) => void
  /** What the scroll position shows: the entry under the top edge, and whether the end is in view. */
  setActive: (entry: DiffEntry | null, atEnd: boolean) => void
  /**
   * Scrolls to a target. Only `reveal` opens a folded file there; a line
   * target always does, since a folded file has no line to show.
   */
  jumpTo: (target: DiffTarget, options?: { reveal?: boolean }) => void
  /** Opens a finding's note and brings its lines into view. */
  showFinding: (id: string, target: DiffTarget) => void
  setComposer: (target: CommentDraftTarget | null) => void
  setComposerText: (text: string) => void
  setExpandedFinding: (id: string | null) => void
  /** Opens a thread's reply box with this text, or closes it with null. */
  setReplyDraft: (threadId: number, text: string | null) => void
  /** Edits a pending review comment, or stops editing it with null. */
  setPendingEdit: (commentId: number, text: string | null) => void
  setThreadOpen: (threadId: number, open: boolean) => void
  focusChat: () => void
  askInChat: (text: string) => void
  sendInChat: (text: string) => void
  clearChatQuestion: () => void
  attachToChat: (excerpts: ReadonlyArray<CodeExcerpt>) => void
  removeChatExcerpt: (index: number) => void
  clearChatExcerpts: () => void
  showOpenConversations: () => void
  showFindings: () => void
  /** Whether the event was waiting, consuming it so it's handled once. */
  takeEvent: (event: PageEvent) => boolean
  toggleCollapsed: (readKey: string) => void
  /** Folds or opens the file being read, keeping it where it is. */
  toggleActiveCollapsed: () => void
  setEntryOrder: (entries: ReadonlyArray<DiffEntry>) => void
  setFileFilter: (filter: string) => void
  setStandingInView: (inView: boolean) => void
  /** Marks an entry viewed (or not); from the file being read, moves on to the next unread one. */
  markViewed: (id: string) => void
  /** Marks every entry of a walkthrough step read, or all unread when they already are. */
  markStepRead: (index: number) => void
  /** The next file not yet viewed, in reading order, after the one being read. */
  jumpToUnviewed: () => void
}

const ORDER_KEY = "open-swe.review.view"
const DIFF_STYLE_KEY = "open-swe.review.diffStyle"
const NAVIGATOR_KEY = "open-swe.review.navigator"
/** Wide enough for the file list, the diff and the chat side by side. */
export const NAVIGATOR_INLINE_QUERY = "(min-width: 1360px)"

function viewedKey(pr: PullRequestRef, headSha: string | null): string {
  return `open-swe.review.viewed.${pr.owner}/${pr.repo}/${pr.number}.${headSha ?? ""}`
}

function samePullRequest(a: PullRequestRef | null, b: PullRequestRef): boolean {
  return (
    !!a && a.owner === b.owner && a.repo === b.repo && a.number === b.number
  )
}

function readViewed(pr: PullRequestRef, headSha: string | null): Set<string> {
  const raw = readPreference(viewedKey(pr, headSha))
  if (!raw) return new Set()
  try {
    const parsed: unknown = JSON.parse(raw)
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === "string")
        : []
    )
  } catch (error) {
    console.warn("Could not parse viewed files", error)
    return new Set()
  }
}

function excerptKey(excerpt: CodeExcerpt): string {
  return `${excerpt.path}:${excerpt.lineLabel}`
}

/** Whether the entry was marked viewed: its whole file, or its own slice of it. */
export function isRead(
  viewed: ReadonlySet<string>,
  entry: Pick<DiffEntry, "file" | "readKey">
): boolean {
  return viewed.has(entry.file.path) || viewed.has(entry.readKey)
}

/** Read entries start collapsed; the viewer can flip either way. */
export function isCollapsed(
  state: Pick<ReviewPageState, "viewed" | "collapsed">,
  entry: Pick<DiffEntry, "file" | "readKey">
): boolean {
  return isRead(state.viewed, entry) !== state.collapsed.has(entry.readKey)
}

/**
 * The walkthrough step the reader is in. At the bottom of the page that's the last step,
 * even though the entry under the top edge, which keyboard actions use, may be earlier.
 */
export function activeStep(
  state: Pick<ReviewPageState, "entryOrder" | "activeEntry" | "atEnd">
): number | null {
  if (state.atEnd) return state.entryOrder.at(-1)?.step?.index ?? null
  return (
    state.entryOrder.find((entry) => entry.id === state.activeEntry)?.step
      ?.index ?? null
  )
}

/** A step the reader scrolled through to its end without marking it read: worth a nudge. */
export function isPassed(
  state: Pick<
    ReviewPageState,
    "entryOrder" | "activeEntry" | "visitedSteps" | "atEnd"
  >,
  step: { index: number; count: number }
): boolean {
  // Reaching the bottom of the page scrolls the last step fully past the reader.
  if (state.atEnd && step.index === step.count) return true
  if (!state.visitedSteps.has(step.index)) return false
  return (activeStep(state) ?? 0) > step.index
}

function storedPreferences(): Preferences {
  return {
    navigatorOpen: readPreference(NAVIGATOR_KEY) !== "closed",
    // A walkthrough, when one exists, is the default reading order.
    order: readPreference(ORDER_KEY) === "files" ? "files" : "guide",
    diffStyle: readPreference(DIFF_STYLE_KEY) === "split" ? "split" : "unified",
  }
}

function freshHead(
  pr: PullRequestRef | null,
  headSha: string | null
): HeadState {
  return {
    viewedSha: headSha,
    viewed: pr ? readViewed(pr, headSha) : new Set(),
    collapsed: new Set(),
  }
}

function freshPullRequest(pr: PullRequestRef | null): PullRequestState {
  return {
    pr,
    composer: null,
    composerText: "",
    replyDrafts: new Map(),
    pendingEdits: new Map(),
    chatExcerpts: [],
  }
}

function freshVisit(): VisitState {
  return {
    railTab: "chat",
    railOpen: false,
    navigatorOverlay: false,
    reviewOpen: false,
    reviewVerdict: "COMMENT",
    activePath: null,
    activeEntry: null,
    visitedSteps: new Set(),
    atEnd: false,
    jump: null,
    expandedFinding: null,
    threadsOpen: new Map(),
    chatDraft: undefined,
    chatQuestion: undefined,
    events: new Set(),
    entryOrder: [],
    fileFilter: "",
    standingInView: true,
    lastStop: null,
  }
}

function withKey<K, V>(
  map: ReadonlyMap<K, V>,
  key: K,
  value: V | null
): ReadonlyMap<K, V> {
  const next = new Map(map)
  if (value === null) next.delete(key)
  else next.set(key, value)
  return next
}

function flipped(set: ReadonlySet<string>, key: string): ReadonlySet<string> {
  const next = new Set(set)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

export const useReviewPage = create<ReviewPageState & ReviewPageActions>()((
  set,
  get
) => {
  // A file split across steps is viewed once every slice is; until then each slice is read alone.
  const setRead = (targets: ReadonlyArray<DiffEntry>, read: boolean) => {
    const { pr, viewed, viewedSha, collapsed, entryOrder } = get()
    const next = new Set(viewed)
    const flips = new Set(collapsed)
    const slicesOf = new Map<string, Array<DiffEntry>>()
    for (const other of entryOrder) {
      if (other.readKey === other.file.path) continue
      const slices = slicesOf.get(other.file.path) ?? []
      slices.push(other)
      slicesOf.set(other.file.path, slices)
    }
    for (const entry of targets) {
      const path = entry.file.path
      const slices = slicesOf.get(path) ?? []
      flips.delete(entry.readKey)
      if (read) {
        next.add(entry.readKey)
        if (slices.every((slice) => next.has(slice.readKey))) {
          next.add(path)
          for (const slice of slices) next.delete(slice.readKey)
        }
      } else if (next.has(path)) {
        next.delete(path)
        for (const slice of slices)
          if (slice.readKey !== entry.readKey) next.add(slice.readKey)
      } else {
        next.delete(entry.readKey)
      }
    }
    if (pr) writePreference(viewedKey(pr, viewedSha), JSON.stringify([...next]))
    set({ viewed: next, collapsed: flips })
  }

  const emit = (event: PageEvent) =>
    set({ events: new Set(get().events).add(event) })

  return {
    navigatorOpen: true,
    order: "files",
    diffStyle: "unified",
    ...freshHead(null, null),
    ...freshPullRequest(null),
    ...freshVisit(),

    open: (pr, headSha) => {
      const samePr = samePullRequest(get().pr, pr)
      if (samePr && get().viewedSha === headSha) return
      set({
        ...(samePr
          ? {}
          : {
              ...storedPreferences(),
              ...freshPullRequest(pr),
              ...freshVisit(),
            }),
        ...freshHead(pr, headSha),
      })
    },
    leave: () => set(freshVisit()),
    setRailTab: (railTab) => set({ railTab, railOpen: true }),
    setRailOpen: (railOpen) => set({ railOpen }),
    toggleNavigator: () => {
      if (!window.matchMedia(NAVIGATOR_INLINE_QUERY).matches) {
        set({ navigatorOverlay: !get().navigatorOverlay })
        return
      }
      const navigatorOpen = !get().navigatorOpen
      writePreference(NAVIGATOR_KEY, navigatorOpen ? "open" : "closed")
      set({ navigatorOpen })
    },
    setNavigatorOverlay: (navigatorOverlay) => set({ navigatorOverlay }),
    openReview: (verdict = "COMMENT") =>
      set({ reviewOpen: true, reviewVerdict: verdict }),
    setReviewOpen: (reviewOpen) => set({ reviewOpen }),
    setOrder: (order) => {
      writePreference(ORDER_KEY, order)
      set({ order })
    },
    setDiffStyle: (diffStyle) => {
      writePreference(DIFF_STYLE_KEY, diffStyle)
      set({ diffStyle })
    },
    setActive: (entry, atEnd) => {
      const { activeEntry, visitedSteps } = get()
      const step = entry?.step?.index
      const visit = step !== undefined && !visitedSteps.has(step)
      if (
        activeEntry === (entry?.id ?? null) &&
        get().atEnd === atEnd &&
        !visit
      )
        return
      set({
        activeEntry: entry?.id ?? null,
        activePath: entry?.file.path ?? null,
        atEnd,
        ...(visit ? { visitedSteps: new Set(visitedSteps).add(step) } : {}),
      })
    },
    jumpTo: (target, { reveal = target.kind === "line" } = {}) => {
      // Claim the target now so a key pressed before the scroll settles moves on from it.
      const landing =
        target.kind === "top" ? undefined : findEntry(get().entryOrder, target)
      const opens =
        reveal &&
        landing &&
        isRenderable(landing.file) &&
        isCollapsed(get(), landing)
      set({
        jump: { key: (get().jump?.key ?? 0) + 1, target },
        navigatorOverlay: false,
        ...(landing
          ? { activeEntry: landing.id, activePath: landing.file.path }
          : {}),
        ...(opens
          ? { collapsed: flipped(get().collapsed, landing.readKey) }
          : {}),
      })
    },
    showFinding: (id, target) => {
      set({ expandedFinding: id })
      get().jumpTo(target, { reveal: true })
    },
    setComposer: (composer) => {
      const current = get().composer
      if (!composer) {
        set({ composer: null, composerText: "" })
        return
      }
      if (current && get().composerText.trim()) {
        const { hi, side } = rangeBounds(current.range)
        toast("Finish or cancel the comment you started first", {
          id: "review-composer-busy",
        })
        get().jumpTo({ kind: "line", path: current.path, line: hi, side })
        return
      }
      set({ composer, composerText: "" })
    },
    setComposerText: (composerText) => set({ composerText }),
    setExpandedFinding: (expandedFinding) => set({ expandedFinding }),
    setReplyDraft: (threadId, text) =>
      set({ replyDrafts: withKey(get().replyDrafts, threadId, text) }),
    setPendingEdit: (commentId, text) =>
      set({ pendingEdits: withKey(get().pendingEdits, commentId, text) }),
    setThreadOpen: (threadId, open) =>
      set({ threadsOpen: withKey(get().threadsOpen, threadId, open) }),
    showOpenConversations: () => {
      set({ railTab: "discussion", railOpen: true })
      emit("showOpenConversations")
    },
    showFindings: () => {
      emit("showFindings")
      get().jumpTo({ kind: "top" })
    },
    takeEvent: (event) => {
      if (!get().events.has(event)) return false
      const events = new Set(get().events)
      events.delete(event)
      set({ events })
      return true
    },
    focusChat: () => {
      set({ railTab: "chat", railOpen: true })
      focusChatComposer()
    },
    askInChat: (text) => {
      set({ chatDraft: { key: (get().chatDraft?.key ?? 0) + 1, text } })
      get().focusChat()
    },
    sendInChat: (chatQuestion) => {
      set({ chatQuestion })
      get().focusChat()
    },
    clearChatQuestion: () => set({ chatQuestion: undefined }),
    attachToChat: (excerpts) => {
      const known = new Set(get().chatExcerpts.map(excerptKey))
      set({
        chatExcerpts: [
          ...get().chatExcerpts,
          ...excerpts.filter((item) => !known.has(excerptKey(item))),
        ],
      })
    },
    removeChatExcerpt: (index) =>
      set({
        chatExcerpts: get().chatExcerpts.filter((_, i) => i !== index),
      }),
    clearChatExcerpts: () => set({ chatExcerpts: [] }),
    setEntryOrder: (entryOrder) => set({ entryOrder }),
    setFileFilter: (fileFilter) => set({ fileFilter }),
    setStandingInView: (standingInView) => {
      if (get().standingInView !== standingInView) set({ standingInView })
    },
    markViewed: (id) => {
      const { entryOrder, activeEntry, jumpTo } = get()
      const index = entryOrder.findIndex((entry) => entry.id === id)
      const entry = entryOrder[index]
      if (!entry) return
      const nowViewed = !isRead(get().viewed, entry)
      setRead([entry], nowViewed)
      if (activeEntry !== id) return
      if (!nowViewed) {
        // Keeps the file's top in place as it opens again.
        jumpTo({ kind: "entry", id })
        return
      }
      const { viewed } = get()
      const next = entryOrder
        .slice(index + 1)
        .find((candidate) => !isRead(viewed, candidate))
      if (next) jumpTo({ kind: "entry", id: next.id })
    },
    markStepRead: (index) => {
      const { entryOrder, viewed } = get()
      const entries = entryOrder.filter((entry) => entry.step?.index === index)
      setRead(entries, !entries.every((entry) => isRead(viewed, entry)))
    },
    jumpToUnviewed: () => {
      const { entryOrder, activeEntry, viewed, jumpTo } = get()
      const from = entryOrder.findIndex((entry) => entry.id === activeEntry)
      const next = [
        ...entryOrder.slice(from + 1),
        ...entryOrder.slice(0, from + 1),
      ].find((entry) => !isRead(viewed, entry))
      if (next) jumpTo({ kind: "entry", id: next.id })
    },
    toggleCollapsed: (readKey) =>
      set({ collapsed: flipped(get().collapsed, readKey) }),
    toggleActiveCollapsed: () => {
      const { activeEntry, entryOrder, toggleCollapsed, jumpTo } = get()
      const entry = entryOrder.find(({ id }) => id === activeEntry)
      if (!entry) return
      toggleCollapsed(entry.readKey)
      jumpTo({ kind: "entry", id: entry.id })
    },
  }
})

/**
 * Runs `handle` once for each time the event is emitted, even when this part
 * of the page mounts after the click that emitted it, and never on a later mount.
 */
export function usePageEvent(event: PageEvent, handle: () => void): void {
  const pending = useReviewPage((state) => state.events.has(event))
  const handler = useRef(handle)
  useLayoutEffect(() => {
    handler.current = handle
  })
  useEffect(() => {
    if (pending && useReviewPage.getState().takeEvent(event)) handler.current()
  }, [pending, event])
}

/** The chat composer lives in the agents thread view; reach it through the DOM. */
function focusChatComposer(): void {
  requestAnimationFrame(() => {
    const editor = document.querySelector<HTMLElement>(
      '[data-review-rail] [data-testid="composer-editor"]'
    )
    if (!editor) return
    editor.focus()
    const selection = window.getSelection()
    if (selection) {
      selection.selectAllChildren(editor)
      selection.collapseToEnd()
    }
  })
}
