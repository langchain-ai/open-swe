import { useQueryClient } from "@tanstack/react-query"
import { useLayoutEffect, useMemo, useRef } from "react"
import type { SelectedLineRange } from "@pierre/diffs"

import { useRegisterAppCommands, type AppCommand } from "@/lib/appCommands"
import type { DiffEntry } from "./diffEntries"
import { isAnchored } from "./findings"
import { reviewQueries } from "./queries"
import type { TextSelection } from "./SelectionBar"
import { focusChatComposer, useReviewPage, type DiffTarget } from "./store"

interface Stop {
  path: string
  line: number
  side: "LEFT" | "RIGHT"
  finding: string | null
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
  const live = useRef({ entries, selection, clearSelection, askAboutLines, scrollTo })
  useLayoutEffect(() => {
    live.current = { entries, selection, clearSelection, askAboutLines, scrollTo }
  })
  const stopIndex = useRef(-1)

  const commands = useMemo<Array<AppCommand>>(() => {
    const paths = () => [...new Set(live.current.entries.map((entry) => entry.file.path))]
    const moveFile = (delta: 1 | -1) => {
      const all = paths()
      if (all.length === 0) return
      const { activePath, jumpTo } = useReviewPage.getState()
      const index = activePath ? all.indexOf(activePath) : -1
      const next = all[Math.min(all.length - 1, Math.max(0, index + delta))]
      if (next) jumpTo({ kind: "file", path: next })
    }
    // Findings and open threads in diff order: one queue of things that want attention.
    const stops = (): Array<Stop> => {
      const { pr } = useReviewPage.getState()
      if (!pr) return []
      const detail = queryClient.getQueryData(reviewQueries.detail(pr).queryKey)
      const conversation = queryClient.getQueryData(reviewQueries.conversation(pr).queryKey)
      const order = new Map(paths().map((path, i) => [path, i]))
      const list: Array<Stop> = [
        ...(detail?.findings ?? [])
          .filter((finding) => finding.status === "open" && isAnchored(finding))
          .map((finding) => ({
            path: finding.file,
            line: finding.end_line as number,
            side: finding.side,
            finding: finding.id,
          })),
        ...(conversation?.threads ?? [])
          .filter((thread) => !thread.resolved && !thread.outdated && thread.line !== null)
          .map((thread) => ({
            path: thread.path,
            line: thread.line as number,
            side: thread.side,
            finding: null,
          })),
      ]
      return list
        .filter((stop) => order.has(stop.path))
        .sort((a, b) => order.get(a.path)! - order.get(b.path)! || a.line - b.line)
    }
    const moveStop = (delta: 1 | -1) => {
      const all = stops()
      if (all.length === 0) return
      stopIndex.current = (stopIndex.current + delta + all.length) % all.length
      const stop = all[stopIndex.current]!
      const { setExpandedFinding, jumpTo } = useReviewPage.getState()
      if (stop.finding) setExpandedFinding(stop.finding)
      jumpTo({ kind: "line", path: stop.path, line: stop.line, side: stop.side })
    }
    return [
      { id: "review-next-file", label: "Next file", shortcuts: ["j"], group: "Pull request", run: () => moveFile(1) },
      { id: "review-previous-file", label: "Previous file", shortcuts: ["k"], group: "Pull request", run: () => moveFile(-1) },
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
          const { activePath, toggleViewed, viewed, jumpTo } = useReviewPage.getState()
          if (!activePath) return
          if (!toggleViewed(activePath)) return
          const all = paths()
          const after = all.slice(all.indexOf(activePath) + 1)
          const next = after.find((path) => !viewed.has(path))
          if (next) jumpTo({ kind: "file", path: next })
        },
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
