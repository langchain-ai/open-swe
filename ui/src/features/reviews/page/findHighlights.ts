import { useCallback, useEffect, useRef, type RefObject } from "react"

import { useReviewPage } from "./store"

const HIGHLIGHT = "review-find"

/** How a hit looks inside a diff's shadow root, where the page's own CSS can't reach. */
export const FIND_HIGHLIGHT_CSS = `::highlight(${HIGHLIGHT}){background-color:var(--bg-warning-subtle);color:var(--text-warning-primary)}`

/** The query's occurrences in one row's text, as ranges over its text nodes. */
function rowRanges(row: Element, needle: string): Array<Range> {
  const nodes: Array<Text> = []
  const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode())
    if (node instanceof Text) nodes.push(node)
  const text = nodes.map((node) => node.data).join("")
  const haystack = text.toLowerCase()
  const ranges: Array<Range> = []
  // Where each node starts in the row's text, to turn an offset into a node and position.
  const starts: Array<number> = []
  let length = 0
  for (const node of nodes) {
    starts.push(length)
    length += node.data.length
  }
  const point = (offset: number, end: boolean): [Text, number] => {
    let index = nodes.length - 1
    while (
      index > 0 &&
      (end ? starts[index]! >= offset : starts[index]! > offset)
    )
      index--
    return [nodes[index]!, offset - starts[index]!]
  }
  for (
    let at = haystack.indexOf(needle);
    at >= 0;
    at = haystack.indexOf(needle, at + needle.length)
  ) {
    const range = new Range()
    range.setStart(...point(at, false))
    range.setEnd(...point(at + needle.length, true))
    ranges.push(range)
  }
  return ranges
}

/** Marks every hit in the code rows on screen; the virtualized rest is marked as it renders. */
function paint(root: HTMLElement | null, query: string) {
  if (typeof CSS === "undefined" || !CSS.highlights) return
  if (!root || !query.trim()) {
    CSS.highlights.delete(HIGHLIGHT)
    return
  }
  const needle = query.toLowerCase()
  const ranges: Array<Range> = []
  for (const host of root.querySelectorAll("diffs-container"))
    for (const row of host.shadowRoot?.querySelectorAll(
      "[data-content] [data-line]"
    ) ?? [])
      ranges.push(...rowRanges(row, needle))
  CSS.highlights.set(HIGHLIGHT, new Highlight(...ranges))
}

/**
 * Keeps the find bar's hits marked in the diff under `root`; call the
 * returned function whenever rows may have been drawn (scrolls, renders).
 */
export function useFindHighlights(
  root: RefObject<HTMLElement | null>
): () => void {
  const frame = useRef(0)
  const schedule = useCallback(() => {
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => {
      const { findOpen, findQuery } = useReviewPage.getState()
      paint(root.current, findOpen ? findQuery : "")
    })
  }, [root])
  useEffect(
    () =>
      useReviewPage.subscribe((state, previous) => {
        if (
          state.findOpen !== previous.findOpen ||
          state.findQuery !== previous.findQuery
        )
          schedule()
      }),
    [schedule]
  )
  useEffect(
    () => () => {
      cancelAnimationFrame(frame.current)
      if (typeof CSS !== "undefined") CSS.highlights?.delete(HIGHLIGHT)
    },
    []
  )
  return schedule
}
