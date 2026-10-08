import { create } from "zustand"
import { toast } from "sonner"
import type { SelectedLineRange } from "@pierre/diffs"

import type { PullRequestReviewEvent } from "@/lib/api"
import type { DiffStyle } from "@/features/agents/utils/diffUtils"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"
import type { PullRequestRef } from "./queries"

export type RailTab = "chat" | "discussion"
export type DiffOrder = "files" | "guide"

/** Where the diff should scroll: a file, or a line in it. */
export type DiffTarget =
  | { kind: "top" }
  | { kind: "file"; path: string }
  | { kind: "entry"; id: string }
  | {
      kind: "line"
      path: string
      line: number
      side: "LEFT" | "RIGHT"
      /** First line of a multi-line target; the whole range is highlighted. */
      start?: number
    }

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
  /** What's typed in the composer; kept here so a half-written comment is never moved or lost. */
  composerText: string
  expandedFinding: string | null
  chatDraft: { key: number; text: string } | undefined
  /** Code attached to the next chat message, shown as chips above the composer. */
  chatExcerpts: ReadonlyArray<CodeExcerpt>
  /** Bumped to bring the discussion's open conversations into view. */
  openConversationsKey: number
  /** Bumped to open Open SWE's findings in the status panel and bring them into view. */
  findingsKey: number
  /** Files whose collapsed state the viewer flipped away from the default (viewed = collapsed). */
  collapsed: ReadonlySet<string>
  /** The diff's entries in reading order. */
  entryOrder: ReadonlyArray<{ id: string; path: string }>
  /** Narrows the diff, the file tree and the walkthrough to matching paths. */
  fileFilter: string
  /** Whether the status card is on screen; when it isn't, the header carries its sentence. */
  standingInView: boolean
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
  setComposerText: (text: string) => void
  setExpandedFinding: (id: string | null) => void
  askInChat: (text: string) => void
  attachToChat: (excerpts: ReadonlyArray<CodeExcerpt>, question: string) => void
  removeChatExcerpt: (index: number) => void
  clearChatExcerpts: () => void
  showOpenConversations: () => void
  showFindings: () => void
  toggleCollapsed: (path: string) => void
  setEntryOrder: (entries: ReadonlyArray<{ id: string; path: string }>) => void
  setFileFilter: (filter: string) => void
  setStandingInView: (inView: boolean) => void
  /** Marks an entry's file viewed (or not); from the file being read, moves on to the next unread one. */
  markViewed: (id: string) => void
  /** The next file not yet viewed, in reading order, after the one being read. */
  jumpToUnviewed: () => void
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
    composerText: "",
    expandedFinding: null,
    chatDraft: undefined,
    chatExcerpts: [],
    openConversationsKey: 0,
    findingsKey: 0,
    collapsed: new Set(),
    entryOrder: [],
    fileFilter: "",
    standingInView: true,

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
              composerText: "",
              expandedFinding: null,
              chatDraft: undefined,
              chatExcerpts: [],
              fileFilter: "",
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
      // Claim the target now so a key pressed before the scroll settles moves on from it.
      const landing =
        target.kind === "entry"
          ? get().entryOrder.find((entry) => entry.id === target.id)
          : target.kind === "file"
            ? get().entryOrder.find((entry) => entry.path === target.path)
            : undefined
      set({
        jump: { key: jumpKey, target },
        navigatorOverlay: false,
        ...(landing
          ? { activeEntry: landing.id, activePath: landing.path }
          : {}),
      })
    },
    setComposer: (composer) => {
      const current = get().composer
      if (!composer) {
        set({ composer: null, composerText: "" })
        return
      }
      if (current && get().composerText.trim()) {
        const end = Math.max(current.range.start, current.range.end)
        toast("Finish or cancel the comment you started first", {
          id: "review-composer-busy",
        })
        get().jumpTo({
          kind: "line",
          path: current.path,
          line: end,
          side:
            (current.range.endSide ?? current.range.side) === "deletions"
              ? "LEFT"
              : "RIGHT",
        })
        return
      }
      set({ composer, composerText: "" })
    },
    setComposerText: (composerText) => set({ composerText }),
    setExpandedFinding: (expandedFinding) => set({ expandedFinding }),
    showOpenConversations: () =>
      set({
        railTab: "discussion",
        railOpen: true,
        openConversationsKey: get().openConversationsKey + 1,
      }),
    showFindings: () => {
      set({ findingsKey: get().findingsKey + 1 })
      get().jumpTo({ kind: "top" })
    },
    askInChat: (text) => {
      const key = (get().chatDraft?.key ?? 0) + 1
      set({ chatDraft: { key, text }, railTab: "chat", railOpen: true })
      focusChatComposer()
    },
    attachToChat: (excerpts, question) => {
      const known = new Set(
        get().chatExcerpts.map((item) => `${item.path}:${item.lineLabel}`)
      )
      set({
        chatExcerpts: [
          ...get().chatExcerpts,
          ...excerpts.filter(
            (item) => !known.has(`${item.path}:${item.lineLabel}`)
          ),
        ],
      })
      if (question) get().askInChat(question)
      else {
        set({ railTab: "chat", railOpen: true })
        focusChatComposer()
      }
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
      const { entryOrder, activeEntry, toggleViewed, jumpTo } = get()
      const index = entryOrder.findIndex((entry) => entry.id === id)
      const entry = entryOrder[index]
      if (!entry) return
      const reading = activeEntry === id
      const nowViewed = toggleViewed(entry.path)
      if (!reading) return
      const viewed = get().viewed
      const next = nowViewed
        ? entryOrder
            .slice(index + 1)
            .find((candidate) => !viewed.has(candidate.path))
        : undefined
      jumpTo({ kind: "entry", id: (next ?? entry).id })
    },
    jumpToUnviewed: () => {
      const { entryOrder, activeEntry, viewed, jumpTo } = get()
      const from = entryOrder.findIndex((entry) => entry.id === activeEntry)
      const ordered = [
        ...entryOrder.slice(from + 1),
        ...entryOrder.slice(0, from + 1),
      ]
      const next = ordered.find((entry) => !viewed.has(entry.path))
      if (next) jumpTo({ kind: "entry", id: next.id })
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
