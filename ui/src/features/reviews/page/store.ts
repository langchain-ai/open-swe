import { create } from "zustand"
import type { SelectedLineRange } from "@pierre/diffs"

import type { PullRequestReviewEvent } from "@/lib/api"
import type { DiffStyle } from "@/features/agents/utils/diffUtils"
import type { PullRequestRef } from "./queries"

export type RailTab = "chat" | "discussion"
export type DiffOrder = "files" | "guide"

/** Where the diff should scroll: a file, or a line in it. */
export type DiffTarget =
  | { kind: "top" }
  | { kind: "file"; path: string }
  | { kind: "entry"; id: string }
  | { kind: "line"; path: string; line: number; side: "LEFT" | "RIGHT" }

export interface CommentDraftTarget {
  path: string
  range: SelectedLineRange
}

interface ReviewPageState {
  pr: PullRequestRef | null
  railTab: RailTab
  /** Below the wide breakpoint the rail is an overlay; on wide screens it is the focus-mode toggle. */
  railOpen: boolean
  /** Whether the file list sits beside the diff, where there is room for it. */
  navigatorOpen: boolean
  /** The file list over the page, where there is no room beside the diff. */
  navigatorOverlay: boolean
  reviewOpen: boolean
  reviewVerdict: PullRequestReviewEvent
  order: DiffOrder
  diffStyle: DiffStyle
  viewed: ReadonlySet<string>
  /** The file, and its diff entry, under the top of the diff viewport. */
  activePath: string | null
  activeEntry: string | null
  /** Bumped per request so asking for the same target twice still scrolls. */
  jump: { key: number; target: DiffTarget } | null
  composer: CommentDraftTarget | null
  expandedFinding: string | null
  chatDraft: { key: number; text: string } | undefined
  /** Files whose collapsed state the viewer flipped away from the default (viewed = collapsed). */
  collapsed: ReadonlySet<string>
}

interface ReviewPageActions {
  open: (pr: PullRequestRef, headSha: string | null) => void
  setRailTab: (tab: RailTab) => void
  setRailOpen: (open: boolean) => void
  toggleNavigator: () => void
  setNavigatorOverlay: (open: boolean) => void
  openReview: (verdict?: PullRequestReviewEvent) => void
  setReviewOpen: (open: boolean) => void
  setOrder: (order: DiffOrder) => void
  setDiffStyle: (style: DiffStyle) => void
  toggleViewed: (path: string) => boolean
  setActive: (entry: { id: string; path: string } | null) => void
  jumpTo: (target: DiffTarget) => void
  setComposer: (target: CommentDraftTarget | null) => void
  setExpandedFinding: (id: string | null) => void
  askInChat: (text: string) => void
  toggleCollapsed: (path: string) => void
}

const ORDER_KEY = "open-swe.review.view"
const DIFF_STYLE_KEY = "open-swe.review.diffStyle"
const NAVIGATOR_KEY = "open-swe.review.navigator"
/** Wide enough for the file list, the diff and the chat side by side. */
export const NAVIGATOR_INLINE_QUERY = "(min-width: 1360px)"

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch (error) {
    console.warn("Could not read a review page preference", { key, error })
    return null
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch (error) {
    console.warn("Could not save a review page preference", { key, error })
  }
}

function viewedKey(pr: PullRequestRef, headSha: string | null): string {
  return `open-swe.review.viewed.${pr.owner}/${pr.repo}/${pr.number}.${headSha ?? ""}`
}

function samePullRequest(a: PullRequestRef | null, b: PullRequestRef): boolean {
  return (
    !!a && a.owner === b.owner && a.repo === b.repo && a.number === b.number
  )
}

function readViewed(pr: PullRequestRef, headSha: string | null): Set<string> {
  const raw = read(viewedKey(pr, headSha))
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

let headShaForViewed: string | null = null
let jumpKey = 0

export const useReviewPage = create<ReviewPageState & ReviewPageActions>()(
  (set, get) => ({
    pr: null,
    railTab: "chat",
    railOpen: false,
    navigatorOpen: true,
    navigatorOverlay: false,
    reviewOpen: false,
    reviewVerdict: "COMMENT",
    order: "files",
    diffStyle: "unified",
    viewed: new Set(),
    activePath: null,
    activeEntry: null,
    jump: null,
    composer: null,
    expandedFinding: null,
    chatDraft: undefined,
    collapsed: new Set(),

    open: (pr, headSha) => {
      const samePr = samePullRequest(get().pr, pr)
      if (samePr && headShaForViewed === headSha) return
      headShaForViewed = headSha
      const storedOrder = read(ORDER_KEY)
      set({
        pr,
        viewed: readViewed(pr, headSha),
        ...(samePr
          ? {}
          : {
              railTab: "chat",
              reviewOpen: false,
              activePath: null,
              activeEntry: null,
              jump: null,
              composer: null,
              expandedFinding: null,
              chatDraft: undefined,
              collapsed: new Set(),
              // A walkthrough, when one exists, is the default reading order.
              order: storedOrder === "files" ? "files" : "guide",
              diffStyle: read(DIFF_STYLE_KEY) === "split" ? "split" : "unified",
              navigatorOpen: read(NAVIGATOR_KEY) !== "closed",
            }),
      })
    },
    setRailTab: (railTab) => set({ railTab, railOpen: true }),
    setRailOpen: (railOpen) => set({ railOpen }),
    toggleNavigator: () => {
      if (!window.matchMedia(NAVIGATOR_INLINE_QUERY).matches) {
        set({ navigatorOverlay: !get().navigatorOverlay })
        return
      }
      const navigatorOpen = !get().navigatorOpen
      write(NAVIGATOR_KEY, navigatorOpen ? "open" : "closed")
      set({ navigatorOpen })
    },
    setNavigatorOverlay: (navigatorOverlay) => set({ navigatorOverlay }),
    openReview: (verdict = "COMMENT") =>
      set({ reviewOpen: true, reviewVerdict: verdict }),
    setReviewOpen: (reviewOpen) => set({ reviewOpen }),
    setOrder: (order) => {
      write(ORDER_KEY, order)
      set({ order })
    },
    setDiffStyle: (diffStyle) => {
      write(DIFF_STYLE_KEY, diffStyle)
      set({ diffStyle })
    },
    toggleViewed: (path) => {
      const { pr, viewed } = get()
      const next = new Set(viewed)
      const nowViewed = !next.has(path)
      if (nowViewed) next.add(path)
      else next.delete(path)
      if (pr) write(viewedKey(pr, headShaForViewed), JSON.stringify([...next]))
      const collapsed = new Set(get().collapsed)
      collapsed.delete(path)
      set({ viewed: next, collapsed })
      return nowViewed
    },
    setActive: (entry) => {
      const activeEntry = entry?.id ?? null
      if (get().activeEntry !== activeEntry)
        set({ activeEntry, activePath: entry?.path ?? null })
    },
    jumpTo: (target) => {
      jumpKey += 1
      set({ jump: { key: jumpKey, target }, navigatorOverlay: false })
    },
    setComposer: (composer) => set({ composer }),
    setExpandedFinding: (expandedFinding) => set({ expandedFinding }),
    askInChat: (text) => {
      const key = (get().chatDraft?.key ?? 0) + 1
      set({ chatDraft: { key, text }, railTab: "chat", railOpen: true })
      focusChatComposer()
    },
    toggleCollapsed: (path) => {
      const next = new Set(get().collapsed)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      set({ collapsed: next })
    },
  })
)

/** The chat composer lives in the agents thread view; reach it through the DOM. */
export function focusChatComposer(): void {
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
