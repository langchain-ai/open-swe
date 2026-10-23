/** A file-relative scroll position. */
export interface ScrollAnchor {
  path: string
  offset: number
}
const anchorPattern = /^(-?\d+)@(.+)$/
export function parseScrollAnchor(
  value: string | undefined
): ScrollAnchor | null {
  const match = value ? anchorPattern.exec(value) : null
  return match ? { offset: Number(match[1]), path: match[2]! } : null
}

export function formatScrollAnchor(anchor: ScrollAnchor): string {
  return `${anchor.offset}@${anchor.path}`
}
