import { useQuery } from "@tanstack/react-query"

import { usePageTool } from "@/lib/pageTools"
import { toPierreSide } from "@/features/reviews/lib/lineRange"
import { buildEntries, matchesFileFilter, type DiffEntry } from "./diffEntries"
import { findingTarget, isAnchored } from "./findings"
import { reviewQueries, type PullRequestRef } from "./queries"
import { NAVIGATOR_INLINE_QUERY, isRead, useReviewPage } from "./store"

const SIDE = {
  type: "string",
  enum: ["RIGHT", "LEFT"],
  description:
    "RIGHT for the pull request's version of the file (added and unchanged lines), LEFT for the base version (removed lines). Defaults to RIGHT.",
} as const

/** How long a tool waits for the diff to lay out entries after it changes the view. */
const LAYOUT_TIMEOUT_MS = 5_000

const LINES = {
  path: { type: "string", description: "The file's path in the repository." },
  start_line: {
    type: "integer",
    description: "First line of the range; leave out for a single line.",
  },
  line: { type: "integer", description: "Last (or only) line of the range." },
  side: SIDE,
} as const

/**
 * The review page's tools for Robin's chat: what it can show the person in
 * the pull request open beside it. Each goes through the page's own store,
 * the same way the person's clicks do.
 */
export function useReviewPageTools(pr: PullRequestRef) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const files = useQuery(reviewQueries.diff(pr)).data?.files ?? []
  const findings = detail?.findings ?? []

  /** Makes sure the file is in the diff and not hidden by the file filter. */
  const ensureShown = (path: string) => {
    if (!files.some((file) => file.path === path))
      throw new Error(`${path} is not one of this pull request's files`)
    const { fileFilter, setFileFilter } = useReviewPage.getState()
    if (fileFilter && !matchesFileFilter(path, fileFilter)) setFileFilter("")
  }

  const walkthrough = detail?.walkthrough ?? null
  const steps = walkthrough?.steps ?? []
  /** Every entry of each walkthrough step, as the diff lays the walkthrough out with no filter. */
  const stepParts = (index: number) =>
    buildEntries(files, walkthrough, "guide").filter(
      (entry) => entry.step?.index === index
    )

  /**
   * The diff's entries `select` picks, once the diff lays any out: a change of
   * order or filter only reaches the store's entries on the diff's next render.
   */
  const laidOut = (
    select: (entries: ReadonlyArray<DiffEntry>) => Array<DiffEntry>,
    missing: string
  ) =>
    new Promise<Array<DiffEntry>>((resolve, reject) => {
      const now = select(useReviewPage.getState().entryOrder)
      if (now.length) {
        resolve(now)
        return
      }
      const timer = setTimeout(() => {
        stop()
        reject(new Error(missing))
      }, LAYOUT_TIMEOUT_MS)
      const stop = useReviewPage.subscribe((state) => {
        const entries = select(state.entryOrder)
        if (!entries.length) return
        clearTimeout(timer)
        stop()
        resolve(entries)
      })
    })

  /** A walkthrough step's entries, switching the diff to the walkthrough's order to show them. */
  const stepEntries = (index: number) => {
    if (!steps.some((step) => step.index === index))
      throw new Error(
        steps.length
          ? `The walkthrough has no step ${index}`
          : "This pull request has no walkthrough yet"
      )
    const parts = new Set(stepParts(index).map((entry) => entry.id))
    if (parts.size === 0)
      throw new Error("The diff is still loading; try again in a moment")
    const { order, setOrder, fileFilter, setFileFilter } =
      useReviewPage.getState()
    if (order !== "guide") setOrder("guide")
    if (fileFilter) setFileFilter("")
    // Waits for all of them: a filter cleared above reaches the diff on its next render.
    return laidOut((entries) => {
      const shown = entries.filter((entry) => parts.has(entry.id))
      return shown.length === parts.size ? shown : []
    }, `Step ${index} did not appear in the diff`)
  }

  usePageTool({
    name: "review_list_files",
    description:
      "List the pull request's changed files in the order the page shows them, with what the person has marked viewed, the file they are reading and the current file filter.",
    annotations: { readOnlyHint: true },
    execute: () => {
      const state = useReviewPage.getState()
      // The page's reading order first, then any files the filter hides.
      const order = [
        ...new Set(state.entryOrder.map((entry) => entry.file.path)),
      ]
      const rank = (path: string) => {
        const index = order.indexOf(path)
        return index === -1 ? order.length : index
      }
      const viewed = (path: string) => {
        if (state.viewed.has(path)) return true
        const entries = state.entryOrder.filter(
          (entry) => entry.file.path === path
        )
        return (
          entries.length > 0 &&
          entries.every((entry) => isRead(state.viewed, entry))
        )
      }
      return {
        reading: state.activePath,
        order: state.order,
        filter: state.fileFilter,
        files: [...files]
          .sort((a, b) => rank(a.path) - rank(b.path))
          .map((file) => ({
            path: file.path,
            status: file.status,
            additions: file.additions,
            deletions: file.deletions,
            shown: order.includes(file.path),
            viewed: viewed(file.path),
          })),
      }
    },
  })

  usePageTool({
    name: "review_show_code",
    description:
      "Scroll the diff to a file, or to a line or range of lines in it, and highlight the lines briefly so the person sees what you are pointing at.",
    inputSchema: {
      type: "object",
      properties: {
        path: LINES.path,
        start_line: LINES.start_line,
        line: {
          type: "integer",
          description:
            "Last (or only) line to show; leave out to show the top of the file.",
        },
        side: SIDE,
      },
      required: ["path"],
    } as const,
    execute: ({ path, start_line, line, side }) => {
      ensureShown(path)
      const { jumpTo } = useReviewPage.getState()
      if (line === undefined) jumpTo({ kind: "file", path }, { reveal: true })
      else
        jumpTo({
          kind: "line",
          path,
          line,
          start: start_line,
          side: side ?? "RIGHT",
        })
      return { shown: { path, start_line, line } }
    },
  })

  usePageTool({
    name: "review_list_findings",
    description:
      "List Robin's findings on this pull request: each one's id, title, severity group, status and where it is.",
    annotations: { readOnlyHint: true },
    execute: () =>
      findings.map((finding) => ({
        id: finding.id,
        title: finding.title,
        group: finding.group,
        status: finding.status,
        path: finding.file,
        start_line: finding.start_line,
        line: finding.end_line,
        side: finding.side,
        on_screen: isAnchored(finding),
      })),
  })

  usePageTool({
    name: "review_show_finding",
    description:
      "Open one of Robin's findings in the diff: scroll to its lines and expand its note.",
    inputSchema: {
      type: "object",
      properties: {
        id: {
          type: "string",
          description: "The finding's id, from review_list_findings.",
        },
      },
      required: ["id"],
    } as const,
    execute: ({ id }) => {
      const finding = findings.find((candidate) => candidate.id === id)
      if (!finding) throw new Error(`No finding has the id ${id}`)
      if (!isAnchored(finding))
        throw new Error(
          `The finding "${finding.title}" is no longer on a line of the diff`
        )
      ensureShown(finding.file)
      useReviewPage.getState().showFinding(id, findingTarget(finding))
      return { shown: id }
    },
  })

  usePageTool({
    name: "review_filter_files",
    description:
      "Narrow the diff, the file tree and the walkthrough to files whose paths fuzzy-match the filter. An empty filter shows every file again.",
    inputSchema: {
      type: "object",
      properties: {
        filter: {
          type: "string",
          description: "Words to match against file paths.",
        },
      },
      required: ["filter"],
    } as const,
    execute: ({ filter }) => {
      useReviewPage.getState().setFileFilter(filter)
      return {
        filter,
        matching: files
          .filter(
            (file) => !filter.trim() || matchesFileFilter(file.path, filter)
          )
          .map((file) => file.path),
      }
    },
  })

  usePageTool({
    name: "review_highlight_text",
    description:
      "Highlight every occurrence of some text in the diff with the page's find bar, and move to the first one. An empty query closes the find bar.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "The text to highlight." },
      },
      required: ["query"],
    } as const,
    execute: ({ query }) => {
      const { setFindOpen, setFindQuery, setFindIndex } =
        useReviewPage.getState()
      setFindQuery(query)
      setFindIndex(0)
      setFindOpen(query.length > 0)
      return { highlighted: query }
    },
  })

  usePageTool({
    name: "review_set_view",
    description:
      "Change how the page shows the pull request. Leave out anything to keep it as it is.",
    inputSchema: {
      type: "object",
      properties: {
        order: {
          type: "string",
          enum: ["guide", "files"],
          description:
            "guide follows Robin's walkthrough of the change step by step; files lists files in path order.",
        },
        diff_style: {
          type: "string",
          enum: ["unified", "split"],
          description:
            "unified shows one column; split shows the base and the change side by side.",
        },
        file_list: {
          type: "boolean",
          description: "Whether the list of files sits beside the diff.",
        },
      },
    } as const,
    execute: ({ order, diff_style, file_list }) => {
      const state = useReviewPage.getState()
      if (order) state.setOrder(order)
      if (diff_style) state.setDiffStyle(diff_style)
      // Where there is no room beside the diff, the file list opens over the page.
      const inline = window.matchMedia(NAVIGATOR_INLINE_QUERY).matches
      const fileListShown = (current: typeof state) =>
        inline ? current.navigatorOpen : current.navigatorOverlay
      if (file_list !== undefined && file_list !== fileListShown(state))
        state.toggleNavigator()
      const now = useReviewPage.getState()
      return {
        order: now.order,
        diff_style: now.diffStyle,
        file_list: fileListShown(now),
      }
    },
  })

  usePageTool({
    name: "review_draft_comment",
    description:
      "Open a review comment on a line or range of lines with your text filled in. It is only a draft: the person reads it, edits it if they like, and posts or discards it themselves.",
    inputSchema: {
      type: "object",
      properties: {
        ...LINES,
        body: {
          type: "string",
          description: "The comment, in GitHub Markdown.",
        },
      },
      required: ["path", "line", "body"],
    } as const,
    execute: ({ path, start_line, line, side, body }) => {
      const started = useReviewPage.getState()
      if (started.composer && started.composerText.trim())
        throw new Error(
          "The person is already writing a comment; it has to be posted or cancelled first"
        )
      ensureShown(path)
      const pierreSide = toPierreSide(side ?? "RIGHT")
      const { setComposer } = useReviewPage.getState()
      setComposer({
        path,
        range: { start: start_line ?? line, end: line, side: pierreSide },
      })
      const { setComposerText, jumpTo } = useReviewPage.getState()
      setComposerText(body)
      jumpTo({
        kind: "line",
        path,
        line,
        start: start_line,
        side: side ?? "RIGHT",
      })
      return { drafted: { path, start_line, line } }
    },
  })
  usePageTool({
    name: "review_list_steps",
    description:
      "List the steps of Robin's walkthrough of this pull request, in reading order: each step's index, title, summary and files, and whether the person has marked all of it viewed.",
    annotations: { readOnlyHint: true },
    execute: () => {
      const { order, viewed } = useReviewPage.getState()
      return {
        order,
        steps: steps.map((step) => ({
          index: step.index,
          title: step.title,
          summary: step.summary,
          files: step.files.map((file) => file.path),
          viewed: stepParts(step.index).every((entry) => isRead(viewed, entry)),
        })),
      }
    },
  })

  usePageTool({
    name: "review_show_step",
    description:
      "Move the diff to one step of Robin's walkthrough, showing it from its header. Switches the diff to the walkthrough's order if it follows file order.",
    inputSchema: {
      type: "object",
      properties: {
        index: {
          type: "integer",
          description: "The step's index, from review_list_steps.",
        },
      },
      required: ["index"],
    } as const,
    execute: async ({ index }) => {
      const [first] = await stepEntries(index)
      if (first)
        useReviewPage.getState().jumpTo({ kind: "entry", id: first.id })
      return { shown: index }
    },
  })

  usePageTool({
    name: "review_mark_step_viewed",
    description:
      "Mark every file of one walkthrough step as viewed, or as not viewed, the way the person's own checkbox does. Viewed files fold away in the diff.",
    inputSchema: {
      type: "object",
      properties: {
        index: {
          type: "integer",
          description: "The step's index, from review_list_steps.",
        },
        viewed: {
          type: "boolean",
          description:
            "true to mark it viewed (the default), false to unmark it.",
        },
      },
      required: ["index"],
    } as const,
    execute: async ({ index, viewed = true }) => {
      const entries = await stepEntries(index)
      const state = useReviewPage.getState()
      const read = entries.filter((entry) => isRead(state.viewed, entry))
      // The step's own toggle marks all read unless all already are, then all unread.
      if (
        viewed ? read.length < entries.length : read.length === entries.length
      )
        state.markStepRead(index)
      else if (!viewed)
        for (const entry of read) useReviewPage.getState().markViewed(entry.id)
      return { step: index, viewed }
    },
  })

  usePageTool({
    name: "review_mark_file_viewed",
    description:
      "Mark one changed file as viewed, or as not viewed, the way the person's own checkbox does. A viewed file folds away in the diff.",
    inputSchema: {
      type: "object",
      properties: {
        path: LINES.path,
        viewed: {
          type: "boolean",
          description:
            "true to mark it viewed (the default), false to unmark it.",
        },
      },
      required: ["path"],
    } as const,
    execute: async ({ path, viewed = true }) => {
      ensureShown(path)
      const entries = await laidOut(
        (all) => all.filter((entry) => entry.file.path === path),
        `${path} did not appear in the diff`
      )
      for (const entry of entries)
        if (isRead(useReviewPage.getState().viewed, entry) !== viewed)
          useReviewPage.getState().markViewed(entry.id)
      return { path, viewed }
    },
  })
}
