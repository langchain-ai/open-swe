/**
 * Marks the sentences of a PR description the walkthrough annotated.
 *
 * The scout quotes each sentence from the markdown source; this finds it in
 * the rendered text of a paragraph, heading or table cell, however emphasis,
 * code and links split it, and wraps every piece of it in a
 * `<span data-description-note>` the page's renderer turns into a hover target.
 * The last piece also carries `data-description-note-end`, where the
 * disclosure button and popover sit.
 */

interface MdNode {
  type: string
  value?: string
  children?: Array<MdNode>
  data?: {
    hName?: string
    hProperties?: Record<string, string>
  }
}

const BLOCKS = new Set(["paragraph", "heading", "tableCell"])
const INLINE_CONTAINERS = new Set([
  "emphasis",
  "strong",
  "delete",
  "link",
  "linkReference",
])

export const NOTE_ATTRIBUTE = "data-description-note"
export const NOTE_END_ATTRIBUTE = "data-description-note-end"
/** The attributes the markdown sanitizer must keep for the marks to survive. */
export const NOTE_ALLOWED_TAGS = {
  span: ["dataDescriptionNote", "dataDescriptionNoteEnd"],
}

/** The text a sentence's markdown renders to, whitespace collapsed. */
export function plainSentence(markdown: string): string {
  return markdown
    .replace(/^\s*(?:[-*+]|\d+[.)]|#{1,6}|>)\s+/, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/`+/g, "")
    .replace(/\*\*|__|~~|\*/g, "")
    .replace(/(^|[^\w])_([^_]+)_(?=[^\w]|$)/g, "$1$2")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
}

interface Leaf {
  node: MdNode
  text: string
}

function leaves(node: MdNode, out: Array<Leaf>): Array<Leaf> {
  for (const child of node.children ?? []) {
    if (child.type === "text" || child.type === "inlineCode")
      out.push({ node: child, text: child.value ?? "" })
    else if (child.type === "break") out.push({ node: child, text: " " })
    else if (INLINE_CONTAINERS.has(child.type)) leaves(child, out)
  }
  return out
}

/** A leaf's run of text inside one note, `[from, to)` in the leaf's own text. */
interface Piece {
  note: number
  from: number
  to: number
  end: boolean
}

function wrap(note: number, end: boolean, child: MdNode): MdNode {
  return {
    type: "descriptionNote",
    data: {
      hName: "span",
      hProperties: {
        dataDescriptionNote: String(note),
        ...(end ? { dataDescriptionNoteEnd: "true" } : {}),
      },
    },
    children: [child],
  }
}

function split(leaf: MdNode, pieces: Array<Piece>): Array<MdNode> {
  if (leaf.type !== "text") {
    const last = pieces.at(-1)!
    return [
      wrap(
        last.note,
        pieces.some((p) => p.end),
        leaf
      ),
    ]
  }
  const value = leaf.value ?? ""
  const out: Array<MdNode> = []
  let at = 0
  for (const piece of pieces) {
    if (piece.from > at)
      out.push({ type: "text", value: value.slice(at, piece.from) })
    out.push(
      wrap(piece.note, piece.end, {
        type: "text",
        value: value.slice(piece.from, piece.to),
      })
    )
    at = piece.to
  }
  if (at < value.length) out.push({ type: "text", value: value.slice(at) })
  return out
}

function replaceLeaves(node: MdNode, pieces: Map<MdNode, Array<MdNode>>) {
  if (!node.children) return
  node.children = node.children.flatMap((child) => {
    replaceLeaves(child, pieces)
    return pieces.get(child) ?? [child]
  })
}

function annotate(block: MdNode, sentences: Array<string>) {
  const found = leaves(block, [])
  // The block's text with whitespace collapsed, and where each character came from.
  let flat = ""
  const origin: Array<{ leaf: number; offset: number }> = []
  found.forEach(({ text }, leaf) => {
    for (let offset = 0; offset < text.length; offset++) {
      if (/\s/.test(text[offset]!)) {
        if (flat === "" || flat.endsWith(" ")) continue
        flat += " "
      } else flat += text[offset]
      origin.push({ leaf, offset })
    }
  })
  const pieces = new Map<number, Array<Piece>>()
  const taken: Array<[number, number]> = []
  sentences.forEach((sentence, note) => {
    if (!sentence) return
    const start = flat.indexOf(sentence)
    const stop = start + sentence.length
    if (start < 0 || taken.some(([a, b]) => start < b && a < stop)) return
    taken.push([start, stop])
    const spans = new Map<number, Piece>()
    for (let i = start; i < stop; i++) {
      const { leaf, offset } = origin[i]!
      const span = spans.get(leaf)
      if (span) span.to = offset + 1
      else spans.set(leaf, { note, from: offset, to: offset + 1, end: false })
    }
    const last = [...spans.keys()].at(-1)!
    spans.get(last)!.end = true
    for (const [leaf, span] of spans)
      pieces.set(leaf, [...(pieces.get(leaf) ?? []), span])
  })
  if (pieces.size === 0) return
  const replaced = new Map<MdNode, Array<MdNode>>()
  for (const [leaf, spans] of pieces)
    replaced.set(
      found[leaf]!.node,
      split(
        found[leaf]!.node,
        spans.sort((a, b) => a.from - b.from)
      )
    )
  replaceLeaves(block, replaced)
}

function visit(node: MdNode, sentences: Array<string>) {
  if (BLOCKS.has(node.type)) annotate(node, sentences)
  else for (const child of node.children ?? []) visit(child, sentences)
}

/** Remark plugin wrapping each of `sentences` (markdown, by note index) where it appears. */
export function remarkDescriptionNotes(sentences: Array<string>) {
  const plain = sentences.map(plainSentence)
  return (tree: MdNode) => visit(tree, plain)
}
