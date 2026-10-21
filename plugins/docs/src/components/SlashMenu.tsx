/**
 * The `/` menu: type a slash at the start of a block to turn it into a
 * heading, list, quote, code block, table or divider.
 */
import { Extension, type Editor, type Range } from "@tiptap/core"
import Suggestion, { type SuggestionProps } from "@tiptap/suggestion"
import { useSyncExternalStore } from "react"

interface Block {
  title: string
  hint: string
  run: (editor: Editor, range: Range) => void
}

const BLOCKS: Block[] = [
  {
    title: "Text",
    hint: "Plain paragraph",
    run: (e, r) => e.chain().focus().deleteRange(r).setParagraph().run(),
  },
  {
    title: "Heading 1",
    hint: "#",
    run: (e, r) =>
      e.chain().focus().deleteRange(r).setHeading({ level: 1 }).run(),
  },
  {
    title: "Heading 2",
    hint: "##",
    run: (e, r) =>
      e.chain().focus().deleteRange(r).setHeading({ level: 2 }).run(),
  },
  {
    title: "Heading 3",
    hint: "###",
    run: (e, r) =>
      e.chain().focus().deleteRange(r).setHeading({ level: 3 }).run(),
  },
  {
    title: "Bulleted list",
    hint: "-",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBulletList().run(),
  },
  {
    title: "Numbered list",
    hint: "1.",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleOrderedList().run(),
  },
  {
    title: "To-do list",
    hint: "[ ]",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleTaskList().run(),
  },
  {
    title: "Quote",
    hint: ">",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run(),
  },
  {
    title: "Code block",
    hint: "```",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCodeBlock().run(),
  },
  {
    title: "Table",
    hint: "3 × 3",
    run: (e, r) =>
      e
        .chain()
        .focus()
        .deleteRange(r)
        .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
        .run(),
  },
  {
    title: "Divider",
    hint: "---",
    run: (e, r) => e.chain().focus().deleteRange(r).setHorizontalRule().run(),
  },
]

interface MenuState {
  items: Block[]
  selected: number
  top: number
  left: number
  choose: (block: Block) => void
}

/** The open menu, if any, shared between the editor plugin and React. */
class MenuStore {
  state: MenuState | null = null
  private readonly listeners = new Set<() => void>()
  set(state: MenuState | null) {
    this.state = state
    for (const listener of this.listeners) listener()
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  snapshot = () => this.state
}

export const slashMenu = new MenuStore()

function place(
  props: SuggestionProps<Block>,
  selected: number,
  shell: () => DOMRect | undefined
) {
  const caret = props.clientRect?.()
  const box = shell()
  slashMenu.set({
    items: props.items,
    selected,
    top: caret && box ? caret.bottom - box.top + 4 : 0,
    left: caret && box ? caret.left - box.left : 0,
    choose: (block) => props.command(block),
  })
}

export function slashCommands(shell: () => DOMRect | undefined) {
  return Extension.create({
    name: "slashCommands",
    addProseMirrorPlugins() {
      return [
        Suggestion<Block>({
          editor: this.editor,
          char: "/",
          startOfLine: true,
          items: ({ query }) =>
            BLOCKS.filter((block) =>
              block.title.toLowerCase().includes(query.toLowerCase())
            ),
          command: ({ editor, range, props }) => props.run(editor, range),
          render: () => {
            let latest: SuggestionProps<Block> | null = null
            return {
              onStart: (props) => {
                latest = props
                place(props, 0, shell)
              },
              onUpdate: (props) => {
                latest = props
                place(props, 0, shell)
              },
              onKeyDown: ({ event }) => {
                const state = slashMenu.state
                if (!state || !latest) return false
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  const step = event.key === "ArrowDown" ? 1 : -1
                  const count = state.items.length || 1
                  place(latest, (state.selected + step + count) % count, shell)
                  return true
                }
                if (event.key === "Enter") {
                  const block = state.items[state.selected]
                  if (block) state.choose(block)
                  return true
                }
                if (event.key === "Escape") {
                  slashMenu.set(null)
                  return true
                }
                return false
              },
              onExit: () => slashMenu.set(null),
            }
          },
        }),
      ]
    },
  })
}

export function SlashMenu() {
  const state = useSyncExternalStore(
    slashMenu.subscribe,
    slashMenu.snapshot,
    () => null
  )
  if (!state || state.items.length === 0) return null
  return (
    <div
      role="listbox"
      className="absolute z-20 w-60 overflow-hidden rounded-lg border border-default bg-surface-level-1 p-space-1 shadow-lg"
      style={{ top: state.top, left: state.left }}
      onMouseDown={(event) => event.preventDefault()}
    >
      {state.items.map((block, index) => (
        <button
          key={block.title}
          type="button"
          role="option"
          aria-selected={index === state.selected}
          className={`flex w-full items-center justify-between rounded-md px-space-2 py-space-1 text-left text-sm text-primary ${
            index === state.selected
              ? "bg-surface-level-2-hover"
              : "hover:bg-surface-level-2-hover"
          }`}
          onClick={() => state.choose(block)}
        >
          {block.title}
          <span className="text-xs text-tertiary">{block.hint}</span>
        </button>
      ))}
    </div>
  )
}
