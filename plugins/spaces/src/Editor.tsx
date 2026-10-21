import Image from "@tiptap/extension-image"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import Placeholder from "@tiptap/extension-placeholder"
import { TableKit } from "@tiptap/extension-table"
import { Markdown } from "@tiptap/markdown"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import { EditorContent, useEditor, type Editor } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import { useEffect, useRef, useState } from "react"

import { anchorFor, Highlights, setHighlights, type Highlight } from "./anchors"
import type { Anchor } from "./model"

export interface SelectionAction {
  label: string
  run: (anchor: Anchor) => void
}

/**
 * The page's rich editor over its Markdown. Selecting text offers `actions`
 * (comment, suggest, ask the agent) for that passage.
 */
export function PageEditor({
  markdown,
  editable,
  highlights,
  activeId,
  actions,
  onSelectComment,
  onReady,
  onChange,
}: {
  markdown: string
  editable: boolean
  highlights: Highlight[]
  activeId: string | null
  actions: SelectionAction[]
  onSelectComment: (id: string) => void
  onReady: (editor: Editor) => void
  onChange: (markdown: string, doc: ProseMirrorNode) => void
}) {
  const shell = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState<{
    anchor: Anchor
    top: number
    left: number
  } | null>(null)
  const callbacks = useRef({ onSelectComment, onChange })
  callbacks.current = { onSelectComment, onChange }

  const editor = useEditor(
    {
      extensions: [
        StarterKit.configure({ link: { openOnClick: false } }),
        TableKit,
        TaskList,
        TaskItem.configure({ nested: true }),
        Image,
        Placeholder.configure({ placeholder: "Start writing…" }),
        Markdown,
        Highlights.configure({
          onSelect: (id) => callbacks.current.onSelectComment(id),
        }),
      ],
      content: markdown,
      contentType: "markdown",
      editable,
      immediatelyRender: true,
      onUpdate: ({ editor: updated }) =>
        callbacks.current.onChange(updated.getMarkdown(), updated.state.doc),
    },
    [markdown]
  )

  useEffect(() => {
    if (editor) onReady(editor)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per editor.
  }, [editor])

  useEffect(() => {
    if (editor && editor.isEditable !== editable) editor.setEditable(editable)
  }, [editor, editable])

  // The DOM selection, not ProseMirror's: a read-only page has no editor
  // selection, and comments belong on published pages too.
  useEffect(() => {
    if (!editor) return
    const update = () => {
      const selected = document.getSelection()
      const box = shell.current?.getBoundingClientRect()
      if (!selected || selected.isCollapsed || !selected.rangeCount || !box)
        return setSelection(null)
      const range = selected.getRangeAt(0)
      const root = editor.view.dom
      if (
        !root.contains(range.startContainer) ||
        !root.contains(range.endContainer)
      )
        return setSelection(null)
      let anchor: Anchor | null = null
      try {
        const from = editor.view.posAtDOM(
          range.startContainer,
          range.startOffset
        )
        const to = editor.view.posAtDOM(range.endContainer, range.endOffset)
        anchor = anchorFor(
          editor.state.doc,
          Math.min(from, to),
          Math.max(from, to)
        )
      } catch {
        anchor = null
      }
      if (!anchor?.quote.trim()) return setSelection(null)
      const rect = range.getBoundingClientRect()
      setSelection({
        anchor,
        top: rect.top - box.top - 40,
        left: Math.max(0, rect.left - box.left),
      })
    }
    document.addEventListener("selectionchange", update)
    return () => document.removeEventListener("selectionchange", update)
  }, [editor])

  useEffect(() => {
    if (editor) setHighlights(editor.view, highlights, activeId)
  }, [editor, highlights, activeId])

  return (
    <div ref={shell} className="editor-shell">
      {selection && actions.length > 0 && (
        <div
          className="selection-menu"
          style={{ top: selection.top, left: selection.left }}
          onMouseDown={(event) => event.preventDefault()}
        >
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              onClick={() => {
                action.run(selection.anchor)
                setSelection(null)
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
      <EditorContent editor={editor} className="page-body" />
    </div>
  )
}
