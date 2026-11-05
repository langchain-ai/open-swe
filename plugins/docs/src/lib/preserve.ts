/**
 * Saving a page changes only what its editors changed.
 *
 * The editor rewrites Markdown in its own style (padded tables, its own list
 * markers) and drops what it can't show (HTML comments, reference
 * definitions), so saving its output verbatim would turn a one-word edit into
 * a diff of the whole file. Instead a save works in blocks, the runs of lines
 * between blank lines: each block of the file is matched with what the editor
 * made of it, and only blocks someone edited take the editor's version. Every
 * other block, and the blank lines around it, keeps its exact bytes, line
 * endings included; lines the editor wrote take the file's usual ending.
 */
import { diffArrays } from "diff"

// A backtick fence's info string can't hold a backtick.
const FENCE = /^ {0,3}(`{3,}(?=[^`]*$)|~{3,})/
const FENCE_END = /^ {0,3}(`{3,}|~{3,})\s*$/

interface Span {
  from: number
  to: number
}

/** A line and the ending after it; `""` takes the file's usual one. */
interface Line {
  text: string
  end: string
}

/** Markdown as blocks: runs of non-blank lines, each fenced code block whole. */
class Blocks {
  readonly lines: Line[][] = []
  /** The blank lines before each block, and then those after the last. */
  readonly gaps: Line[][] = [[]]
  readonly texts: string[]

  /** With `ownEndings`, each line keeps its ending in `markdown`. */
  constructor(markdown: string, ownEndings = true) {
    let block: Line[] | null = null
    let fence = ""
    const pieces = markdown ? markdown.split("\n") : []
    for (const [index, piece] of pieces.entries()) {
      const last = index === pieces.length - 1
      const crlf = !last && piece.endsWith("\r")
      const line = {
        text: crlf ? piece.slice(0, -1) : piece,
        end: !ownEndings || last ? "" : crlf ? "\r\n" : "\n",
      }
      if (!fence && !line.text.trim()) {
        block = null
        this.gaps.at(-1)?.push(line)
        continue
      }
      if (!block) {
        block = []
        this.lines.push(block)
        this.gaps.push([])
      }
      block.push(line)
      if (!fence) fence = FENCE.exec(line.text)?.[1] ?? ""
      else {
        const end = FENCE_END.exec(line.text)?.[1] ?? ""
        if (end[0] === fence[0] && end.length >= fence.length) fence = ""
      }
    }
    this.texts = this.lines.map((lines) =>
      lines.map((line) => line.text).join("\n")
    )
  }

  /**
   * What `edited` did to this: the block of `edited` each block was kept as
   * (`null` if it was changed or removed), and the blocks added before each.
   */
  changesTo(edited: Blocks) {
    const kept: Array<number | null> = []
    const added = new Map<number, number[]>()
    let to = 0
    for (const chunk of diffArrays(this.texts, edited.texts)) {
      const count = chunk.count ?? chunk.value.length
      for (let i = 0; i < count; i++) {
        if (chunk.added)
          added.set(kept.length, [...(added.get(kept.length) ?? []), to++])
        else kept.push(chunk.removed ? null : to++)
      }
    }
    return { kept, added }
  }
}

/** A block of the file as written, or of the page as edited. */
interface Unit {
  from: "written" | "edited"
  index: number
}

/** The file with the edits made to it, put together block by block. */
class Save {
  private readonly written: Blocks
  /** The file as the editor writes it, which `edited` is an edit of. */
  private readonly normalized: Blocks
  private readonly edited: Blocks
  /** Blocks of the file the editor drops entirely, such as HTML comments. */
  private readonly hidden = new Set<number>()
  private readonly kept: Array<number | null>
  private readonly added: Map<number, number[]>
  private readonly units: Unit[] = []
  /** The next block of `normalized` whose added blocks are still to place. */
  private point = 0

  constructor(
    written: string,
    edited: string,
    private readonly normalize: (markdown: string) => string
  ) {
    this.written = new Blocks(written)
    this.normalized = new Blocks(normalize(written.replaceAll("\r\n", "\n")))
    this.edited = new Blocks(edited, false)
    ;({ kept: this.kept, added: this.added } = this.normalized.changesTo(
      this.edited
    ))
    for (const piece of this.pieces()) this.place(piece)
    this.addUpTo(this.normalized.texts.length)
  }

  /** The saved file's lines. */
  lines(): Line[] {
    const out = [...(this.written.gaps[0] ?? [])]
    this.units.forEach((unit, i) => {
      const before = this.units[i - 1]
      if (before) {
        const gap = this.gapBetween(before, unit)
        out.push(...(gap?.length ? gap : [{ text: "", end: "" }]))
      }
      const from = unit.from === "written" ? this.written : this.edited
      out.push(...(from.lines[unit.index] ?? []))
    })
    if (this.written.lines.length > 0)
      out.push(...(this.written.gaps.at(-1) ?? []))
    return out
  }

  /** The file's own blank lines wherever they border one of its blocks. */
  private gapBetween(before: Unit, unit: Unit): Line[] | undefined {
    if (unit.from === "written" && unit.index > 0)
      return this.written.gaps[unit.index]
    if (before.from === "written") return this.written.gaps[before.index + 1]
    if (unit.from === "edited") return this.edited.gaps[unit.index]
    return undefined
  }

  /**
   * The blocks of `normalized` each block of the file became, if they can be
   * told: a block not in `normalized` as written is normalised on its own.
   */
  private spans(): Array<Span | null> {
    const verbatim = new Set(this.normalized.texts)
    const owners: number[] = []
    const becomes: string[] = []
    this.written.texts.forEach((text, index) => {
      const blocks = verbatim.has(text)
        ? [text]
        : new Blocks(this.normalize(text)).texts
      if (blocks.length === 0) this.hidden.add(index)
      for (const block of blocks) {
        owners.push(index)
        becomes.push(block)
      }
    })
    const found = this.written.texts.map((): Array<number | null> => [])
    let at = 0
    let to = 0
    for (const chunk of diffArrays(becomes, this.normalized.texts)) {
      const count = chunk.count ?? chunk.value.length
      for (let i = 0; i < count; i++) {
        if (chunk.added) to++
        else found[owners[at++] ?? -1]?.push(chunk.removed ? null : to++)
      }
    }
    return found.map((blocks) => {
      const [from] = blocks
      return typeof from === "number" && blocks.every((k, i) => k === from + i)
        ? { from, to: from + blocks.length }
        : null
    })
  }

  /**
   * The file in pieces that are each kept or replaced whole: a block with
   * what it became, and the blocks between those with what they became.
   */
  private pieces(): Array<{ written: Span; normalized: Span }> {
    const spans = this.spans()
    const end = this.normalized.texts.length
    const pieces: Array<{ written: Span; normalized: Span }> = []
    let w = 0
    let n = 0
    for (const [index, span] of [...spans, { from: end, to: end }].entries()) {
      if (!span) continue
      if (w < index || n < span.from)
        pieces.push({
          written: { from: w, to: index },
          normalized: { from: n, to: span.from },
        })
      if (index < spans.length)
        pieces.push({
          written: { from: index, to: index + 1 },
          normalized: span,
        })
      w = index + 1
      n = span.to
    }
    return pieces
  }

  private place(piece: { written: Span; normalized: Span }) {
    const { from, to } = piece.normalized
    let touched = false
    for (let k = from; k < to; k++)
      if (this.kept[k] === null || (k > from && this.added.has(k)))
        touched = true
    if (!touched) {
      this.addUpTo(from)
      this.keep(piece.written.from, piece.written.to)
      return
    }
    // The editor's version, with what was added right around it, goes where
    // the first block it can show was; blocks it can't show stay either side.
    let shown = piece.written.from
    while (shown < piece.written.to && this.hidden.has(shown)) shown++
    this.keep(piece.written.from, shown)
    for (let k = from; k <= to; k++) {
      this.addUpTo(k)
      const index = k < to ? this.kept[k] : null
      if (typeof index === "number") this.units.push({ from: "edited", index })
    }
    this.keep(shown, piece.written.to, true)
  }

  /** Places the blocks someone added before each block of `normalized` up to `until`. */
  private addUpTo(until: number) {
    for (; this.point <= until; this.point++)
      for (const index of this.added.get(this.point) ?? [])
        this.units.push({ from: "edited", index })
  }

  private keep(from: number, to: number, hiddenOnly = false) {
    for (let index = from; index < to; index++)
      if (!hiddenOnly || this.hidden.has(index))
        this.units.push({ from: "written", index })
  }
}

/** The line ending most of `text`'s lines end with. */
function usualEnding(text: string): string {
  const crlf = text.split("\r\n").length - 1
  return crlf > text.split("\n").length - 1 - crlf ? "\r\n" : "\n"
}

/**
 * `current`, what the editors made of `original`, saved over `original` in
 * its own line endings; `normalize` is how the editor rewrites Markdown.
 */
export function preserveFormatting(
  original: string,
  current: string,
  normalize: (markdown: string) => string
): string {
  const eol = usualEnding(original)
  const lines = new Save(original, current, normalize).lines()
  return lines
    .map(({ text, end }, i) =>
      i < lines.length - 1 ? text + (end || eol) : text
    )
    .join("")
}
