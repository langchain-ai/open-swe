/**
 * Comments point at text, not at line numbers: each keeps the quoted passage
 * and a little context either side, and is found again in whatever the page
 * says now. A passage that no longer exists leaves its comment orphaned.
 */
import { Extension } from "@tiptap/core"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import { Plugin, PluginKey } from "@tiptap/pm/state"
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view"

import type { Anchor } from "./model"

const CONTEXT = 32

interface TextIndex {
  text: string
  /** The document position of each character of `text`; -1 between blocks. */
  positions: number[]
}

function textIndex(doc: ProseMirrorNode): TextIndex {
  let text = ""
  const positions: number[] = []
  doc.descendants((node, pos) => {
    if (node.isText && node.text) {
      for (let i = 0; i < node.text.length; i++) positions.push(pos + i)
      text += node.text
    } else if (node.isBlock && text && !text.endsWith("\n")) {
      positions.push(-1)
      text += "\n"
    }
    return true
  })
  return { text, positions }
}

export function anchorFor(
  doc: ProseMirrorNode,
  from: number,
  to: number,
  context = CONTEXT
): Anchor | null {
  const { text, positions } = textIndex(doc)
  const start = positions.findIndex((pos) => pos >= from)
  let end = -1
  for (let i = positions.length - 1; i >= 0; i--)
    if ((positions[i] ?? -1) >= 0 && (positions[i] ?? 0) < to) {
      end = i + 1
      break
    }
  if (start < 0 || end <= start) return null
  return {
    quote: text.slice(start, end),
    prefix: text.slice(Math.max(0, start - context), start),
    suffix: text.slice(end, end + context),
  }
}

/** The text of the last heading before `pos`, the section a passage sits in. */
export function headingBefore(
  doc: ProseMirrorNode,
  pos: number
): string | null {
  let heading: string | null = null
  doc.nodesBetween(0, pos, (node) => {
    if (node.type.name === "heading") heading = node.textContent
    return !node.isBlock || node.type.name === "doc"
  })
  return heading
}

function sharedSuffix(a: string, b: string): number {
  let n = 0
  while (
    n < a.length &&
    n < b.length &&
    a[a.length - 1 - n] === b[b.length - 1 - n]
  )
    n++
  return n
}

function sharedPrefix(a: string, b: string): number {
  let n = 0
  while (n < a.length && n < b.length && a[n] === b[n]) n++
  return n
}

/** Where `anchor`'s quote is now: the occurrence whose surroundings match best. */
export function resolveAnchor(
  doc: ProseMirrorNode,
  anchor: Anchor
): { from: number; to: number } | null {
  if (!anchor.quote) return null
  const { text, positions } = textIndex(doc)
  let best = -1
  let bestScore = -1
  for (
    let at = text.indexOf(anchor.quote);
    at >= 0;
    at = text.indexOf(anchor.quote, at + 1)
  ) {
    const score =
      sharedSuffix(text.slice(Math.max(0, at - CONTEXT), at), anchor.prefix) +
      sharedPrefix(
        text.slice(
          at + anchor.quote.length,
          at + anchor.quote.length + CONTEXT
        ),
        anchor.suffix
      )
    if (score > bestScore) {
      best = at
      bestScore = score
    }
  }
  if (best < 0) return null
  const from = positions[best]
  const last = positions[best + anchor.quote.length - 1]
  if (from === undefined || last === undefined || from < 0 || last < 0)
    return null
  return { from, to: last + 1 }
}

export interface Highlight {
  id: string
  anchor: Anchor
  kind: "comment" | "suggestion"
}

interface HighlightState {
  highlights: Highlight[]
  active: string | null
  decorations: DecorationSet
}

const key = new PluginKey<HighlightState>("docs-highlights")

function decorate(
  doc: ProseMirrorNode,
  highlights: Highlight[],
  active: string | null
): DecorationSet {
  const decorations: Decoration[] = []
  for (const highlight of highlights) {
    const range = resolveAnchor(doc, highlight.anchor)
    if (!range) continue
    decorations.push(
      Decoration.inline(
        range.from,
        range.to,
        {
          class: `highlight highlight-${highlight.kind}${highlight.id === active ? " highlight-active" : ""}`,
          "data-comment-id": highlight.id,
        },
        { id: highlight.id }
      )
    )
  }
  return DecorationSet.create(doc, decorations)
}

export function setHighlights(
  view: EditorView,
  highlights: Highlight[],
  active: string | null
): void {
  view.dispatch(view.state.tr.setMeta(key, { highlights, active }))
}

/** Highlights each comment's passage; clicking one reports its id. */
export const Highlights = Extension.create<{
  onSelect: (id: string) => void
}>({
  name: "docsHighlights",
  addOptions: () => ({ onSelect: () => {} }),
  addProseMirrorPlugins() {
    const onSelect = this.options.onSelect
    return [
      new Plugin<HighlightState>({
        key,
        state: {
          init: (_, state) => ({
            highlights: [],
            active: null,
            decorations: DecorationSet.create(state.doc, []),
          }),
          apply: (tr, previous) => {
            const next = tr.getMeta(key) as
              | { highlights: Highlight[]; active: string | null }
              | undefined
            if (next)
              return {
                ...next,
                decorations: decorate(tr.doc, next.highlights, next.active),
              }
            if (!tr.docChanged) return previous
            return {
              ...previous,
              decorations: decorate(
                tr.doc,
                previous.highlights,
                previous.active
              ),
            }
          },
        },
        props: {
          decorations: (state) => key.getState(state)?.decorations,
          handleClick: (view, pos) => {
            const found = key
              .getState(view.state)
              ?.decorations.find(pos, pos)
              .find((decoration) => typeof decoration.spec.id === "string")
            if (!found) return false
            onSelect(found.spec.id as string)
            return false
          },
        },
      }),
    ]
  },
})
