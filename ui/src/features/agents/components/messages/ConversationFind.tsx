import { Input } from "@langchain/macaw-components/Input"
import { IconButton } from "@langchain/macaw-components/IconButton"
import {
  CaretDownIcon,
  CaretUpIcon,
  XIcon,
} from "@langchain/macaw-components/icons"
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass"
import { useEffect, useRef, useState, type RefObject } from "react"

class ConversationSearch {
  ranges: Range[] = []
  index = 0

  constructor(readonly root: HTMLElement) {}

  find(query: string) {
    this.ranges = []
    if (!query) return this.ranges
    const blocks = new Map<Element, Text[]>()
    const walker = document.createTreeWalker(this.root, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement
      if (
        !(node instanceof Text) ||
        !parent ||
        parent.closest('button, time, svg, [hidden], [aria-hidden="true"]')
      )
        continue
      if (!parent.checkVisibility()) continue
      const block = parent.closest("p, pre, li, td, th, div") ?? parent
      const nodes = blocks.get(block) ?? []
      nodes.push(node)
      blocks.set(block, nodes)
    }
    const needle = query.toLowerCase()
    for (const nodes of blocks.values()) {
      const text = nodes
        .map((node) => node.data)
        .join("")
        .toLowerCase()
      for (
        let at = text.indexOf(needle);
        at >= 0;
        at = text.indexOf(needle, at + needle.length)
      ) {
        const range = new Range()
        let offset = 0
        for (const node of nodes) {
          const end = offset + node.length
          if (at >= offset && at < end) range.setStart(node, at - offset)
          if (at + needle.length > offset && at + needle.length <= end)
            range.setEnd(node, at + needle.length - offset)
          offset = end
        }
        this.ranges.push(range)
      }
    }
    this.ranges.sort((left, right) =>
      left.compareBoundaryPoints(Range.START_TO_START, right)
    )
    return this.ranges
  }

  paint(index: number) {
    this.index = index
    if (!CSS.highlights) return
    CSS.highlights.set("conversation-find", new Highlight(...this.ranges))
    CSS.highlights.set(
      "conversation-find-active",
      new Highlight(...this.ranges.slice(index, index + 1))
    )
  }

  clear() {
    CSS.highlights?.delete("conversation-find")
    CSS.highlights?.delete("conversation-find-active")
  }

  reveal(index: number) {
    const range = this.ranges[index]
    if (!range) return
    const scroller = this.root.closest<HTMLElement>("[data-transcript-scroll]")
    if (!scroller) return
    const rect = range.getBoundingClientRect()
    const viewport = scroller.getBoundingClientRect()
    scroller.scrollTop += rect.top - viewport.top - viewport.height / 2
  }
}

export function ConversationFind({
  root,
  hasEarlier,
}: {
  root: RefObject<HTMLDivElement | null>
  hasEarlier: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [index, setIndex] = useState(0)
  const [count, setCount] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const search = useRef<ConversationSearch | null>(null)
  const previousFocus = useRef<HTMLElement | null>(null)

  const show = () => {
    if (!open)
      previousFocus.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null
    setOpen(true)
    requestAnimationFrame(() => {
      input.current?.focus()
      input.current?.select()
    })
  }
  const close = () => {
    setOpen(false)
    previousFocus.current?.focus({ preventScroll: true })
  }
  const step = (delta: number) => {
    if (!count) return
    const next = (index + delta + count) % count
    setIndex(next)
    search.current?.paint(next)
    search.current?.reveal(next)
  }

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return
      const target = event.target
      if (
        target instanceof Element &&
        target.closest(
          '.monaco-editor, .xterm, [data-hotkeys="ignore"], [role="dialog"]'
        )
      )
        return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault()
        show()
      }
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  })

  useEffect(() => {
    if (!open || !root.current) return
    const finder = new ConversationSearch(root.current)
    search.current = finder
    const refresh = () => {
      const ranges = finder.find(query)
      setCount(ranges.length)
      const next = Math.min(finder.index, Math.max(0, ranges.length - 1))
      finder.paint(next)
      setIndex(next)
    }
    refresh()
    finder.reveal(0)
    const observer = new MutationObserver(refresh)
    observer.observe(root.current, {
      childList: true,
      characterData: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden", "class", "style", "open"],
    })
    return () => {
      observer.disconnect()
      finder.clear()
      search.current = null
    }
  }, [open, query, root])

  return (
    <>
      <style>{`::highlight(conversation-find){background-color:var(--bg-warning-subtle);color:var(--text-warning-primary)}::highlight(conversation-find-active){background-color:var(--bg-warning-strong);color:var(--text-primary)}`}</style>
      {open ? (
        <div
          role="search"
          aria-label="Find in conversation"
          className="absolute top-2 right-3 z-floating-bar max-w-[calc(100%-1.5rem)] rounded-lg border border-default bg-popover p-space-2 shadow-md dark:shadow-none"
        >
          <div
            className="flex items-center gap-space-1"
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault()
                event.stopPropagation()
                close()
              } else if (
                event.key === "Enter" ||
                ((event.metaKey || event.ctrlKey) && event.key === "g")
              ) {
                event.preventDefault()
                step(event.shiftKey ? -1 : 1)
              }
            }}
          >
            <Input
              ref={input}
              size="sm"
              debounceMs={0}
              value={query}
              onChange={(value) => {
                setQuery(value)
                setIndex(0)
              }}
              placeholder="Find in conversation"
              aria-label="Find in conversation"
              spellCheck={false}
              autoComplete="off"
              className="w-48 min-w-0 max-sm:w-32"
            />
            <span
              aria-live="polite"
              className="min-w-16 text-right text-xxs text-secondary tabular-nums"
            >
              {query ? (count ? `${index + 1} of ${count}` : "No results") : ""}
            </span>
            <IconButton
              icon={CaretUpIcon}
              label="Previous match"
              size="xs"
              variant="plain"
              color="secondary"
              disabled={!count}
              onClick={() => step(-1)}
            />
            <IconButton
              icon={CaretDownIcon}
              label="Next match"
              size="xs"
              variant="plain"
              color="secondary"
              disabled={!count}
              onClick={() => step(1)}
            />
            <IconButton
              icon={XIcon}
              label="Close find"
              size="xs"
              variant="plain"
              color="secondary"
              onClick={close}
            />
          </div>
          {hasEarlier && (
            <p className="mt-space-1 text-xxs text-secondary">
              Searching loaded messages only
            </p>
          )}
        </div>
      ) : (
        <div className="absolute top-2 right-3 z-floating-bar">
          <IconButton
            icon={MagnifyingGlassIcon}
            label="Find in conversation (⌘F / Ctrl+F)"
            size="xs"
            variant="plain"
            color="secondary"
            onClick={show}
          />
        </div>
      )}
    </>
  )
}
