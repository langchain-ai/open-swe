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
      onSelectionUpdate: ({ editor: selected }) => {
        const { from, to, empty } = selected.state.selection
        const box = shell.current?.getBoundingClientRect()
        const anchor = empty ? null : anchorFor(selected.state.doc, from, to)
        if (!anchor || !box || !anchor.quote.trim()) return setSelection(null)
        const start = selected.view.coordsAtPos(from)
        setSelection({
          anchor,
          top: start.top - box.top - 40,
          left: Math.max(0, start.left - box.left),
        })
      },
      onBlur: () => setTimeout(() => setSelection(null), 150),
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
