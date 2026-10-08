import { useQueryClient } from "@tanstack/react-query"
import { useLayoutEffect, useMemo, useRef } from "react"
import type { SelectedLineRange } from "@pierre/diffs"

import { useRegisterAppCommands, type AppCommand } from "@/lib/appCommands"
import { toast } from "sonner"

import {
  readStoredDiffOverflow,
  writeStoredDiffOverflow,
} from "@/features/agents/utils/diffUtils"
import { containsLine, type DiffEntry } from "./diffEntries"
import { isAnchored, threadsNeedingAttention } from "./findings"
import { reviewQueries } from "./queries"
import type { TextSelection } from "./SelectionBar"
import { focusChatComposer, useReviewPage, type DiffTarget } from "./store"

interface Stop {
  path: string
  line: number
  start: number | undefined
  side: "LEFT" | "RIGHT"
  finding: string | null
  label: string | null
  order: number
}

/** Review-page shortcuts, registered with the app so `?` lists them beside the global ones. */
export function useDiffKeys({
  entries,
  selection,
  clearSelection,
  askAboutLines,
  scrollTo,
}: {
  entries: ReadonlyArray<DiffEntry>
  selection: TextSelection | null
  clearSelection: () => void
  askAboutLines: (path: string, range: SelectedLineRange) => Promise<void>
  scrollTo: (target: DiffTarget) => void
}) {
  const queryClient = useQueryClient()
  const live = useRef({
    entries,
    selection,
    clearSelection,
    askAboutLines,
    scrollTo,
  })
  useLayoutEffect(() => {
    live.current = {
      entries,
      selection,
      clearSelection,
      askAboutLines,
      scrollTo,
    }
  })
  const stopIndex = useRef(-1)

  const commands = useMemo<Array<AppCommand>>(() => {
    const moveFile = (delta: 1 | -1) => {
      const { entryOrder, activeEntry, jumpTo } = useReviewPage.getState()
      if (entryOrder.length === 0) return
      const index = entryOrder.findIndex((entry) => entry.id === activeEntry)
      const next =
        entryOrder[Math.min(entryOrder.length - 1, Math.max(0, index + delta))]
      if (next) jumpTo({ kind: "entry", id: next.id })
    }
    // Findings and open threads in reading order: one queue of things that want attention.
    const stops = (): Array<Stop> => {
      const { pr } = useReviewPage.getState()
      if (!pr) return []
      const detail = queryClient.getQueryData(reviewQueries.detail(pr).queryKey)
      const conversation = queryClient.getQueryData(
        reviewQueries.conversation(pr).queryKey
      )
      const findings = detail?.findings ?? []
      const shown = live.current.entries
      const position = (stop: Omit<Stop, "order">) =>
        shown.findIndex(
          (entry) =>
            entry.file.path === stop.path &&
            (entry.step === null ||
              containsLine(entry.fileDiff, stop.line, stop.side))
        )
      const list: Array<Omit<Stop, "order">> = [
        ...findings
          .filter((finding) => finding.status === "open" && isAnchored(finding))
          .map((finding) => ({
            path: finding.file,
            line: finding.end_line as number,
            start: finding.start_line ?? undefined,
            side: finding.side,
            finding: finding.id,
            label: finding.title,
          })),
        ...threadsNeedingAttention(conversation?.threads ?? [], findings).map(
          (thread) => ({
            path: thread.path,
            line: thread.line as number,
            start: thread.start_line ?? undefined,
            side: thread.side,
            finding: null,
            label: null,
          })
        ),
      ]
      return list
        .map((stop) => ({ ...stop, order: position(stop) }))
        .filter((stop) => stop.order >= 0)
        .sort((a, b) => a.order - b.order || a.line - b.line)
    }
    const moveStop = (delta: 1 | -1) => {
      const all = stops()
      if (all.length === 0) return
      stopIndex.current = (stopIndex.current + delta + all.length) % all.length
      const stop = all[stopIndex.current]!
      const { setExpandedFinding, jumpTo } = useReviewPage.getState()
      if (stop.finding) setExpandedFinding(stop.finding)
      jumpTo({
        kind: "line",
        path: stop.path,
        line: stop.line,
        start: stop.start,
        side: stop.side,
      })
      const name = stop.path.split("/").pop() ?? stop.path
      toast(
        `${stopIndex.current + 1} of ${all.length} · ${name}:${stop.line}`,
        {
          id: "review-stop",
          description: stop.label ?? "Open conversation",
          duration: 1500,
        }
      )
    }
    return [
      {
        id: "review-next-file",
        label: "Next file",
        shortcuts: ["j"],
        group: "Pull request",
        run: () => moveFile(1),
      },
      {
        id: "review-previous-file",
        label: "Previous file",
        shortcuts: ["k"],
        group: "Pull request",
        run: () => moveFile(-1),
      },
      {
        id: "review-next-note",
        label: "Next finding or open conversation",
        shortcuts: ["n"],
        group: "Pull request",
        run: () => moveStop(1),
      },
      {
        id: "review-previous-note",
        label: "Previous finding or open conversation",
        shortcuts: ["p"],
        group: "Pull request",
        run: () => moveStop(-1),
      },
      {
        id: "review-toggle-viewed",
        label: "Mark file viewed and go to the next",
        shortcuts: ["v"],
        group: "Pull request",
        run: () => {
          const { activeEntry, markViewed } = useReviewPage.getState()
          if (activeEntry) markViewed(activeEntry)
        },
      },
      {
        id: "review-collapse-file",
        label: "Collapse or expand the file",
        shortcuts: ["x"],
        group: "Pull request",
        run: () => {
          const { activeEntry, activePath, toggleCollapsed, jumpTo } =
            useReviewPage.getState()
          if (!activeEntry || !activePath) return
          toggleCollapsed(activePath)
          jumpTo({ kind: "entry", id: activeEntry })
        },
      },
      {
        id: "review-wrap",
        label: "Wrap long lines",
        shortcuts: ["w"],
        group: "Pull request",
        run: () =>
          writeStoredDiffOverflow(
            readStoredDiffOverflow() === "wrap" ? "scroll" : "wrap"
          ),
      },
      {
        id: "review-ask-selection",
        label: "Ask Open SWE about the selected code",
        shortcuts: ["mod+l"],
        group: "Pull request",
        run: () => {
          const current = live.current.selection
          if (!current) return
          void live.current.askAboutLines(current.path, current.range)
          live.current.clearSelection()
        },
      },
      {
        id: "review-focus-chat",
        label: "Focus the chat",
        shortcuts: ["mod+;"],
        group: "Pull request",
        run: () => {
          useReviewPage.getState().setRailTab("chat")
          focusChatComposer()
        },
      },
      {
        id: "review-submit",
        label: "Review changes",
        shortcuts: ["shift+r"],
        group: "Pull request",
        run: () => useReviewPage.getState().openReview(),
      },
      {
        id: "review-toggle-navigator",
        label: "Show or hide the file list",
        shortcuts: ["f"],
        group: "Pull request",
        run: () => useReviewPage.getState().toggleNavigator(),
      },
      {
        id: "review-diff-style",
        label: "Switch between unified and split diffs",
        shortcuts: ["shift+d"],
        group: "Pull request",
        run: () => {
          const { diffStyle, setDiffStyle } = useReviewPage.getState()
          setDiffStyle(diffStyle === "split" ? "unified" : "split")
        },
      },
      {
        id: "review-top",
        label: "Back to the top",
        shortcuts: ["g"],
        group: "Pull request",
        run: () => live.current.scrollTo({ kind: "top" }),
      },
    ]
  }, [queryClient])

  useRegisterAppCommands(commands)
}
