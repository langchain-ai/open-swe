import { useEffect, useRef, type RefObject } from "react"

/** A scroll position as an offset from the top of one file's row, so it survives content above it changing. */
export interface ScrollAnchor {
  path: string
  offset: number
}

export const FILE_ANCHOR_ATTRIBUTE = "data-file-anchor"

const anchorPattern = /^(-?\d+)@(.+)$/
const restoreForMs = 3_000
const writeAfterMs = 250

export function parseScrollAnchor(
  value: string | undefined
): ScrollAnchor | null {
  const match = value ? anchorPattern.exec(value) : null
  return match ? { offset: Number(match[1]), path: match[2]! } : null
}

export function formatScrollAnchor(anchor: ScrollAnchor): string {
  return `${anchor.offset}@${anchor.path}`
}

function fileRows(container: HTMLElement): Array<HTMLElement> {
  return Array.from(
    container.querySelectorAll<HTMLElement>(`[${FILE_ANCHOR_ATTRIBUTE}]`)
  )
}

function readAnchor(container: HTMLElement): ScrollAnchor | null {
  const rows = fileRows(container)
  const top = container.getBoundingClientRect().top
  let chosen = rows[0]
  for (const row of rows) {
    if (row.getBoundingClientRect().top > top) break
    chosen = row
  }
  const path = chosen?.getAttribute(FILE_ANCHOR_ATTRIBUTE)
  if (!chosen || !path) return null
  return {
    path,
    offset: Math.round(top - chosen.getBoundingClientRect().top),
  }
}

function applyAnchor(container: HTMLElement, anchor: ScrollAnchor): boolean {
  const row = fileRows(container).find(
    (node) => node.getAttribute(FILE_ANCHOR_ATTRIBUTE) === anchor.path
  )
  if (!row) return false
  container.scrollTop +=
    row.getBoundingClientRect().top -
    container.getBoundingClientRect().top +
    anchor.offset
  return true
}

/**
 * Restores `initial` once `ready`, holding it while late content (diffs) lays out,
 * then reports every settled scroll position through `onChange`.
 */
export function useScrollAnchor(
  ref: RefObject<HTMLElement | null>,
  initial: string | undefined,
  onChange: (value: string | undefined) => void,
  ready: boolean
) {
  const onChangeRef = useRef(onChange)
  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])
  const initialRef = useRef(initial)
  const holdingRef = useRef(false)

  useEffect(() => {
    const container = ref.current
    const anchor = parseScrollAnchor(initialRef.current)
    if (!ready || !container || !anchor) return
    initialRef.current = undefined
    holdingRef.current = true
    applyAnchor(container, anchor)
    const observer = new ResizeObserver(() => applyAnchor(container, anchor))
    for (const child of Array.from(container.children)) observer.observe(child)
    const release = () => {
      holdingRef.current = false
      observer.disconnect()
      window.clearTimeout(timer)
      for (const event of releaseEvents)
        container.removeEventListener(event, release)
    }
    const releaseEvents = ["wheel", "touchstart", "pointerdown", "keydown"]
    for (const event of releaseEvents)
      container.addEventListener(event, release, { passive: true })
    const timer = window.setTimeout(release, restoreForMs)
    return release
  }, [ref, ready])

  useEffect(() => {
    const container = ref.current
    if (!container) return
    let timer: number | undefined
    const onScroll = () => {
      if (holdingRef.current) return
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const anchor = container.scrollTop > 0 ? readAnchor(container) : null
        onChangeRef.current(anchor ? formatScrollAnchor(anchor) : undefined)
      }, writeAfterMs)
    }
    container.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      window.clearTimeout(timer)
      container.removeEventListener("scroll", onScroll)
    }
  }, [ref])
}
