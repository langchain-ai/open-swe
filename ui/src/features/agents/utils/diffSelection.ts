import type { SelectedLineRange, SelectionSide } from "@pierre/diffs"

interface ShadowRootWithSelection {
  getSelection?: () => Selection | null
}

export function readDiffSelection(
  container: Element | null | undefined
): Selection | null {
  const root = container?.shadowRoot
  if (root) {
    const scoped = (root as ShadowRoot & ShadowRootWithSelection).getSelection
    if (typeof scoped === "function") return scoped.call(root)
  }
  return typeof document !== "undefined" ? document.getSelection() : null
}

function lineMetaFromNode(
  node: Node | null
): { line: number; side: SelectionSide } | null {
  const el = node instanceof Element ? node : (node?.parentElement ?? null)
  const lineEl = el?.closest("[data-line]")
  if (!lineEl) return null
  const line = Number(lineEl.getAttribute("data-line"))
  if (!Number.isInteger(line)) return null
  const type = lineEl.getAttribute("data-line-type") ?? ""
  return { line, side: type.includes("deletion") ? "deletions" : "additions" }
}

export function selectedRangeFromDiff(
  container: Element | null | undefined
): SelectedLineRange | null {
  const selection = readDiffSelection(container)
  if (!selection || selection.isCollapsed || selection.rangeCount === 0)
    return null
  const range = selection.getRangeAt(0)
  const start = lineMetaFromNode(range.startContainer)
  const end = lineMetaFromNode(range.endContainer)
  if (!start || !end) return null
  return {
    start: start.line,
    side: start.side,
    end: end.line,
    endSide: end.side,
  }
}
