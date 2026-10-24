import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import Collaboration from "@tiptap/extension-collaboration"
import CollaborationCaret from "@tiptap/extension-collaboration-caret"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import Placeholder from "@tiptap/extension-placeholder"
import { TableKit } from "@tiptap/extension-table"
import { Markdown } from "@tiptap/markdown"
import { EditorContent, useEditor, type Editor } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import { useEffect, useRef, useState } from "react"

import { SlashMenu, slashCommands } from "@/components/SlashMenu"
import {
  anchorFor,
  Highlights,
  setHighlights,
  type Highlight,
} from "@/lib/anchors"
import { repositoryImages, type ImageLoader } from "@/lib/images"
import { DiagramCodeBlock } from "@/lib/mermaid"
import { FIELD, seedFromMarkdown, type LiveDoc } from "@/lib/live"
import type { Anchor } from "@/lib/model"

export interface SelectionActions {
  comment: (anchor: Anchor) => void
  suggest: (anchor: Anchor) => void
  /** The quiet one: AI stays a step behind the people writing. */
  askAgent?: (anchor: Anchor) => void
}

/**
 * The page. Published, it renders the Markdown read-only; in a draft it is the
 * shared live document, seeded from the draft's Markdown the first time anyone
 * opens it. Either way, selecting text offers to comment on it.
 */
export function PageEditor({
  markdown,
  live,
  me,
  highlights,
  activeId,
  actions,
  onSelectComment,
  onLink,
  onReady,
  onChange,
  page,
  loadImage,
}: {
  markdown: string
  live: LiveDoc | null
  me: { name: string; color: string }
  highlights: Highlight[]
  activeId: string | null
  actions: SelectionActions
  onSelectComment: (id: string) => void
  onLink: (href: string) => void
  onReady: (editor: Editor) => void
  onChange: (editor: Editor) => void
  /** The page's path, which relative image paths resolve against. */
  page: string
  loadImage: ImageLoader
}) {
  const shell = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState<{
    anchor: Anchor
    top: number
    left: number
  } | null>(null)
  const callbacks = useRef({
    onSelectComment,
    onChange,
    onLink,
    page,
    loadImage,
  })
  callbacks.current = { onSelectComment, onChange, onLink, page, loadImage }

  const editor = useEditor(
    {
      extensions: [
        StarterKit.configure({
          codeBlock: false,
          link: { openOnClick: false },
          ...(live ? { undoRedo: false } : {}),
        }),
        DiagramCodeBlock,
        TableKit,
        TaskList,
        TaskItem.configure({ nested: true }),
        repositoryImages(
          () => callbacks.current.page,
          () => callbacks.current.loadImage
        ),
        Placeholder.configure({ placeholder: "Write, or type / for blocks" }),
        Markdown,
        Highlights.configure({
          onSelect: (id) => callbacks.current.onSelectComment(id),
        }),
        ...(live
          ? [
              Collaboration.configure({ document: live.doc, field: FIELD }),
              CollaborationCaret.configure({
                provider: live.provider,
                user: me,
              }),
              slashCommands(() => shell.current?.getBoundingClientRect()),
            ]
          : []),
      ],
      ...(live ? {} : { content: markdown, contentType: "markdown" as const }),
      editable: Boolean(live),
      immediatelyRender: true,
      onUpdate: ({ editor: updated }) => callbacks.current.onChange(updated),
      editorProps: {
        handleDOMEvents: {
          click: (_view, event) => {
            const link = (event.target as HTMLElement | null)?.closest("a")
            const href = link?.getAttribute("href")
            if (!href) return false
            event.preventDefault()
            callbacks.current.onLink(href)
            return true
          },
        },
      },
    },
    // A live draft's content is the shared document, not `markdown`.
    [live, live ? null : markdown]
  )

  useEffect(() => {
    if (!editor) return
    if (live) seedFromMarkdown(live.doc, editor, markdown)
    onReady(editor)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per editor.
  }, [editor])

  useEffect(() => {
    if (editor) setHighlights(editor.view, highlights, activeId)
  }, [editor, highlights, activeId])

  // The DOM selection, not ProseMirror's: a published page has no editor
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
        top: rect.top - box.top - 44,
        left: Math.max(0, rect.left - box.left),
      })
    }
    document.addEventListener("selectionchange", update)
    return () => document.removeEventListener("selectionchange", update)
  }, [editor])

  const run = (act: (anchor: Anchor) => void) => {
    if (!selection) return
    act(selection.anchor)
    setSelection(null)
  }

  return (
    <div ref={shell} className="relative">
      {selection && (
        <div
          className="absolute z-20 flex items-center gap-0.5 rounded-lg border border-default bg-surface-level-1 p-0.5 shadow-lg"
          style={{ top: selection.top, left: selection.left }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <MenuButton onClick={() => run(actions.comment)}>Comment</MenuButton>
          <MenuButton onClick={() => run(actions.suggest)}>
            Suggest edit
          </MenuButton>
          {actions.askAgent && (
            <MenuButton
              title="Ask the agent about this passage"
              onClick={() => run(actions.askAgent!)}
            >
              <SparkleIcon size={14} className="text-icon-secondary" />
            </MenuButton>
          )}
        </div>
      )}
      <SlashMenu />
      <EditorContent editor={editor} className="docs-page" />
    </div>
  )
}

function MenuButton({
  children,
  title,
  onClick,
}: {
  children: React.ReactNode
  title?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className="flex h-7 items-center rounded-md px-space-2 text-sm text-primary hover:bg-surface-level-2-hover"
    >
      {children}
    </button>
  )
}
