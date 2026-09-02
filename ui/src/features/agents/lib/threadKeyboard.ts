import type { SidebarThreadItem } from "./sidebarThreads"

export interface RegisteredThreadRow {
  item: SidebarThreadItem
  node: HTMLElement
}

const DOCUMENT_POSITION_PRECEDING = 2
const DOCUMENT_POSITION_FOLLOWING = 4

export function orderThreadRows(
  rows: Iterable<RegisteredThreadRow>
): Array<RegisteredThreadRow> {
  return [...rows]
    .filter((row) => row.node.isConnected)
    .sort((left, right) => {
      const position = left.node.compareDocumentPosition(right.node)
      if (position & DOCUMENT_POSITION_FOLLOWING) return -1
      if (position & DOCUMENT_POSITION_PRECEDING) return 1
      return 0
    })
}

export function adjacentThreadRow(
  rows: ReadonlyArray<RegisteredThreadRow>,
  activeKey: string | undefined,
  direction: -1 | 1
): RegisteredThreadRow | undefined {
  if (rows.length === 0) return undefined
  const index = activeKey
    ? rows.findIndex((row) => row.item.key === activeKey)
    : -1
  if (index < 0) return direction === 1 ? rows[0] : rows.at(-1)
  return rows[index + direction]
}

export function threadRangeKeys(
  rows: ReadonlyArray<RegisteredThreadRow>,
  anchorKey: string,
  targetKey: string
): Set<string> {
  const anchor = rows.findIndex((row) => row.item.key === anchorKey)
  const target = rows.findIndex((row) => row.item.key === targetKey)
  if (anchor < 0 || target < 0) return new Set()
  const start = Math.min(anchor, target)
  const end = Math.max(anchor, target)
  return new Set(rows.slice(start, end + 1).map((row) => row.item.key))
}
