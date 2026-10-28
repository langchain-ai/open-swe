import { Input } from "@langchain/macaw-components/Input"
import {
  CaretDownIcon,
  CaretUpIcon,
  XIcon,
} from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { useEffect, useMemo, useRef } from "react"

import { findEntry } from "./diffEntries"
import { FILE_HEADER_HEIGHT } from "./FileHeader"
import { findMatches, findTarget, type FindMatch } from "./find"
import { useReviewPage } from "./store"

const FIND_INPUT_ID = "review-find-input"

/** Selects the find bar's text, so ⌘F again or a new search types over it. */
export function focusFindInput() {
  const input = document.getElementById(FIND_INPUT_ID)
  if (input instanceof HTMLInputElement) {
    input.focus()
    input.select()
  }
}

/** The hits for the find bar's query in what the diff shows, in reading order. */
export function useFindMatches(): ReadonlyArray<FindMatch> {
  const entryOrder = useReviewPage((state) => state.entryOrder)
  const query = useReviewPage((state) => state.findQuery)
  const open = useReviewPage((state) => state.findOpen)
  return useMemo(
    () => (open ? findMatches(entryOrder, query) : []),
    [entryOrder, query, open]
  )
}

/** Moves to the hit `delta` away, wrapping around the ends. */
export function stepFind(matches: ReadonlyArray<FindMatch>, delta: 1 | -1) {
  if (matches.length === 0) return
  const { findIndex, setFindIndex, jumpTo } = useReviewPage.getState()
  const index = (findIndex + delta + matches.length) % matches.length
  setFindIndex(index)
  jumpTo(findTarget(matches[index]!))
}

/** The first hit at or after the file being read, so a new search starts where the reader is. */
function nearestMatch(matches: ReadonlyArray<FindMatch>): number {
  const { entryOrder, activeEntry } = useReviewPage.getState()
  const from = entryOrder.findIndex((entry) => entry.id === activeEntry)
  if (from <= 0) return 0
  const at = matches.findIndex((match) => {
    const entry = findEntry(entryOrder, findTarget(match))
    return entry !== undefined && entryOrder.indexOf(entry) >= from
  })
  return Math.max(0, at)
}

/** Find in the diff: file names and changed lines, whichever reading order is showing. */
export function FindBar() {
  const open = useReviewPage((state) => state.findOpen)
  const query = useReviewPage((state) => state.findQuery)
  const index = useReviewPage((state) => state.findIndex)
  const setFindOpen = useReviewPage((state) => state.setFindOpen)
  const setFindQuery = useReviewPage((state) => state.setFindQuery)
  const matches = useFindMatches()

  // Typing lands on the nearest hit; reopening on the same query stays put.
  const searched = useRef<string | null>(null)
  useEffect(() => {
    if (!open) {
      searched.current = null
      return
    }
    if (searched.current === query) return
    searched.current = query
    if (matches.length === 0) return
    const { setFindIndex, jumpTo } = useReviewPage.getState()
    const first = nearestMatch(matches)
    setFindIndex(first)
    jumpTo(findTarget(matches[first]!))
  }, [open, query, matches])

  useEffect(() => {
    if (open) focusFindInput()
  }, [open])

  if (!open) return null
  const position =
    matches.length === 0
      ? query.trim()
        ? "No results"
        : ""
      : `${Math.min(index, matches.length - 1) + 1} of ${matches.length}`
  return (
    <div
      role="search"
      aria-label="Find in the changes"
      // Under the sticky file header, so its Viewed box stays reachable.
      style={{ top: FILE_HEADER_HEIGHT + 8 }}
      className="absolute right-4 z-floating-bar flex items-center gap-space-1 rounded-lg border border-default bg-surface-level-1 py-space-1 pr-space-1 pl-space-3 shadow-md"
    >
      <Input
        inputContainerClassName="border-strong/70 [&:hover:not(:focus-within)]:border-strong"
        size="sm"
        debounceMs={0}
        id={FIND_INPUT_ID}
        type="text"
        value={query}
        placeholder="Find in files and changes"
        aria-label="Find in files and changes"
        spellCheck={false}
        autoComplete="off"
        onChange={setFindQuery}
        onKeyDown={(event) => {
          const mod = event.metaKey || event.ctrlKey
          if (event.key === "Escape") {
            event.preventDefault()
            setFindOpen(false)
          } else if (event.key === "Enter" || (mod && event.key === "g")) {
            event.preventDefault()
            stepFind(matches, event.shiftKey ? -1 : 1)
          } else if (mod && event.key === "f") {
            event.preventDefault()
            focusFindInput()
          }
        }}
        className="w-56 max-sm:w-36"
      />
      <span
        aria-live="polite"
        className="min-w-16 shrink-0 text-right text-xxs text-secondary tabular-nums"
      >
        {position}
      </span>
      <IconButton
        icon={CaretUpIcon}
        label="Previous match"
        size="xs"
        variant="plain"
        color="secondary"
        disabled={matches.length === 0}
        onClick={() => stepFind(matches, -1)}
      />
      <IconButton
        icon={CaretDownIcon}
        label="Next match"
        size="xs"
        variant="plain"
        color="secondary"
        disabled={matches.length === 0}
        onClick={() => stepFind(matches, 1)}
      />
      <IconButton
        icon={XIcon}
        label="Close find"
        size="xs"
        variant="plain"
        color="secondary"
        onClick={() => setFindOpen(false)}
      />
    </div>
  )
}
