/**
 * Saving a page changes only what its editors changed.
 *
 * The editor rewrites Markdown in its own style (padded tables, its own list
 * spacing), so saving its output verbatim would turn a one-word edit into a
 * diff of the whole file. Instead the save works out which lines of the
 * editor's normalised original the edit touched, and applies just those to the
 * file as it was written; every region nobody edited keeps its exact bytes.
 */
import { diffArrays } from "diff"

/** For each line of `normalized`, the line of `original` it matches exactly, if any. */
function align(original: string[], normalized: string[]): Array<number | null> {
  const mapped: Array<number | null> = []
  let a = 0
  for (const chunk of diffArrays(original, normalized)) {
    const count = chunk.count ?? chunk.value.length
    if (chunk.added) for (let i = 0; i < count; i++) mapped.push(null)
    else if (chunk.removed) a += count
    else for (let i = 0; i < count; i++) mapped.push(a++)
  }
  return mapped
}

/** What the edit did to `base`: lines removed, and lines inserted before each index. */
function edits(base: string[], next: string[]) {
  const removed = new Set<number>()
  const inserted = new Map<number, string[]>()
  let b = 0
  for (const chunk of diffArrays(base, next)) {
    const count = chunk.count ?? chunk.value.length
    if (chunk.added)
      inserted.set(b, [...(inserted.get(b) ?? []), ...chunk.value])
    else if (chunk.removed) for (let i = 0; i < count; i++) removed.add(b++)
    else b += count
  }
  return { removed, inserted }
}

/**
 * `current` applied to `original` as a patch: `normalized` is `original` as the
 * editor writes it, and `current` is what the editors made of `normalized`.
 */
export function preserveFormatting(
  original: string,
  normalized: string,
  current: string
): string {
  const o = original.split("\n")
  const b = normalized.split("\n")
  const c = current.split("\n")
  const mapped = align(o, b)
  const { removed, inserted } = edits(b, c)
  const out: string[] = []
  let nextOriginal = 0

  for (let k = 0; k <= b.length;) {
    out.push(...(inserted.get(k) ?? []))
    if (k === b.length) break
    const at = mapped[k]
    if (at !== null && at !== undefined) {
      // Lines of the original the normaliser dropped (spacing, mostly) stay.
      out.push(...o.slice(nextOriginal, at))
      if (!removed.has(k)) out.push(o[at] ?? "")
      nextOriginal = at + 1
      k++
      continue
    }
    // A run the normaliser rewrote: the original's lines up to the next match.
    let end = k
    while (
      end < b.length &&
      (mapped[end] === null || mapped[end] === undefined)
    )
      end++
    const until = end < b.length ? (mapped[end] as number) : o.length
    let touched = false
    for (let i = k; i < end; i++)
      if (removed.has(i) || (i > k && inserted.has(i))) touched = true
    if (touched) {
      for (let i = k; i < end; i++) {
        if (i > k) out.push(...(inserted.get(i) ?? []))
        if (!removed.has(i)) out.push(b[i] ?? "")
      }
    } else out.push(...o.slice(nextOriginal, until))
    nextOriginal = until
    k = end
  }
  out.push(...o.slice(nextOriginal))
  return out.join("\n")
}
