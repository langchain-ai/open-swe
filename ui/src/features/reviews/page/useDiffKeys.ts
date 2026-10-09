import { useQueryClient } from "@tanstack/react-query"
import { useMemo } from "react"
import { toast } from "sonner"

import { useRegisterAppCommands, type AppCommand } from "@/lib/appCommands"
import {
  readStoredDiffOverflow,
  writeStoredDiffOverflow,
} from "@/features/agents/utils/diffUtils"
import { entryShows } from "./diffEntries"
import {
  findingTarget,
  isOpenAnchored,
  threadTarget,
  threadsNeedingAttention,
} from "./findings"
import { reviewQueries } from "./queries"
import { useReviewPage, type LineTarget } from "./store"
import { splitPath, withLine } from "./text"

interface Stop {
  target: LineTarget
  finding: { id: string; title: string } | null
  order: number
}

const GROUP = "Pull request"

export const ASK_SELECTION_SHORTCUT = "a"

const stopKey = ({ target }: Stop) =>
  `${target.path}:${target.side}:${target.line}`

/** Review-page shortcuts, registered with the app so `?` lists them beside the global ones. */
export function useDiffKeys(askAboutSelection: () => void) {
  const queryClient = useQueryClient()

  const commands = useMemo<Array<AppCommand>>(() => {
    const page = () => useReviewPage.getState()
    const moveFile = (delta: 1 | -1) => {
      const { entryOrder, activeEntry, jumpTo } = page()
      const index = entryOrder.findIndex((entry) => entry.id === activeEntry)
      const next =
        entryOrder[Math.min(entryOrder.length - 1, Math.max(0, index + delta))]
      if (next) jumpTo({ kind: "entry", id: next.id })
    }
    // Findings and open threads in reading order: one queue of things that want attention.
    const stops = (): Array<Stop> => {
      const { pr } = page()
      if (!pr) return []
      const findings =
        queryClient.getQueryData(reviewQueries.detail(pr).queryKey)?.findings ??
        []
      const threads =
        queryClient.getQueryData(reviewQueries.conversation(pr).queryKey)
          ?.threads ?? []
      const position = (target: LineTarget) =>
        page().entryOrder.findIndex((entry) =>
          entryShows(entry, target.path, target.line, target.side)
        )
      return [
        ...findings.filter(isOpenAnchored).map((finding) => ({
          target: findingTarget(finding),
          finding: { id: finding.id, title: finding.title },
        })),
        ...threadsNeedingAttention(threads, findings).map((thread) => ({
          target: threadTarget(thread),
          finding: null,
        })),
      ]
        .map((stop) => ({ ...stop, order: position(stop.target) }))
        .filter((stop) => stop.order >= 0)
        .sort((a, b) => a.order - b.order || a.target.line - b.target.line)
    }
    const moveStop = (delta: 1 | -1) => {
      const all = stops()
      if (all.length === 0) return
      const { lastStop } = page()
      const from = all.findIndex((stop) => stopKey(stop) === lastStop)
      const index =
        from < 0
          ? delta > 0
            ? 0
            : all.length - 1
          : (from + delta + all.length) % all.length
      const stop = all[index]!
      useReviewPage.setState({ lastStop: stopKey(stop) })
      if (stop.finding) page().showFinding(stop.finding.id, stop.target)
      else page().jumpTo(stop.target)
      toast(
        `${index + 1} of ${all.length} · ${withLine(splitPath(stop.target.path).name, stop.target.line)}`,
        {
          id: "review-stop",
          description: stop.finding?.title ?? "Open conversation",
          duration: 1500,
          // Bottom right is where the chat composer lives.
          position: "top-center",
        }
      )
    }
    const keys: Array<Omit<AppCommand, "group">> = [
      {
        id: "review-next-file",
        label: "Next file",
        shortcuts: ["j"],
        run: () => moveFile(1),
      },
      {
        id: "review-previous-file",
        label: "Previous file",
        shortcuts: ["k"],
        run: () => moveFile(-1),
      },
      {
        id: "review-next-note",
        label: "Next finding or open conversation",
        shortcuts: ["n"],
        run: () => moveStop(1),
      },
      {
        id: "review-previous-note",
        label: "Previous finding or open conversation",
        shortcuts: ["p"],
        run: () => moveStop(-1),
      },
      {
        id: "review-toggle-viewed",
        label: "Mark file viewed and go to the next",
        shortcuts: ["v"],
        run: () => {
          const { activeEntry, markViewed } = page()
          if (activeEntry) markViewed(activeEntry)
        },
      },
      {
        id: "review-collapse-file",
        label: "Collapse or expand the file",
        shortcuts: ["x"],
        run: () => {
          const { activeEntry, activePath, toggleCollapsed, jumpTo } = page()
          if (!activeEntry || !activePath) return
          toggleCollapsed(activePath)
          jumpTo({ kind: "entry", id: activeEntry })
        },
      },
      {
        id: "review-wrap",
        label: "Wrap long lines",
        shortcuts: ["w"],
        run: () =>
          writeStoredDiffOverflow(
            readStoredDiffOverflow() === "wrap" ? "scroll" : "wrap"
          ),
      },
      {
        id: "review-focus-chat",
        label: "Focus the chat",
        shortcuts: ["mod+;"],
        run: () => page().focusChat(),
      },
      {
        id: "review-submit",
        label: "Review changes",
        shortcuts: ["shift+r"],
        run: () => page().openReview(),
      },
      {
        id: "review-toggle-navigator",
        label: "Show or hide the file list",
        shortcuts: ["f"],
        run: () => page().toggleNavigator(),
      },
      {
        id: "review-diff-style",
        label: "Switch between unified and split diffs",
        shortcuts: ["shift+d"],
        run: () => {
          const { diffStyle, setDiffStyle } = page()
          setDiffStyle(diffStyle === "split" ? "unified" : "split")
        },
      },
      {
        id: "review-top",
        label: "Back to the top",
        shortcuts: ["g"],
        run: () => page().jumpTo({ kind: "top" }),
      },
    ]
    return keys.map((command) => ({ ...command, group: GROUP }))
  }, [queryClient])
  useRegisterAppCommands(commands)

  // Apart from the rest, since the selection it reads changes with every drag.
  const ask = useMemo<Array<AppCommand>>(
    () => [
      {
        id: "review-ask-selection",
        label: "Ask Open SWE about the selected code",
        group: GROUP,
        shortcuts: [ASK_SELECTION_SHORTCUT],
        run: askAboutSelection,
      },
    ],
    [askAboutSelection]
  )
  useRegisterAppCommands(ask)
}
