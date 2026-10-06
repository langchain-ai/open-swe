import { useCallback, useEffect, useMemo, useState } from "react"
import { createPortal } from "react-dom"
import { useNavigate } from "@tanstack/react-router"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { captureElement } from "./elementContext"
import type { ElementContext } from "./elementContext"

export const ANNOTATION_DRAFT_KEY = "open-swe.annotation-draft"
interface Annotation {
  context: ElementContext
  note: string
}

export function PageAnnotations() {
  const navigate = useNavigate()
  const [active, setActive] = useState(false)
  const [hovered, setHovered] = useState<Element | null>(null)
  const [selected, setSelected] = useState<ElementContext | null>(null)
  const [note, setNote] = useState("")
  const [annotations, setAnnotations] = useState<Annotation[]>([])
  const [, refresh] = useState(0)
  const toggle = useCallback(() => {
    setActive((value) => !value)
    setHovered(null)
    setSelected(null)
    setNote("")
  }, [])
  const commands = useMemo(
    () => [
      {
        id: "annotate-page",
        label: "Toggle page annotation mode",
        aliases: ["markup", "feedback"],
        shortcuts: ["mod+alt+shift+a"],
        group: "General",
        run: toggle,
      },
    ],
    [toggle]
  )
  useRegisterAppCommands(commands)

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (
        event.code === "KeyA" &&
        (event.metaKey || event.ctrlKey) &&
        event.altKey &&
        event.shiftKey &&
        !event.repeat &&
        !event.isComposing
      ) {
        event.preventDefault()
        event.stopImmediatePropagation()
        toggle()
      }
    }
    window.addEventListener("keydown", shortcut, true)
    return () => window.removeEventListener("keydown", shortcut, true)
  }, [toggle])

  useEffect(() => {
    if (!active) return
    const isOverlay = (target: EventTarget | null) =>
      target instanceof Element &&
      Boolean(target.closest("[data-page-annotations]"))
    const move = (event: PointerEvent) => {
      if (selected || isOverlay(event.target)) {
        setHovered(null)
        return
      }
      setHovered(event.target instanceof Element ? event.target : null)
    }
    const click = (event: MouseEvent) => {
      if (isOverlay(event.target)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      if (!selected && event.target instanceof Element) {
        setSelected(captureElement(event.target))
        setHovered(null)
        setNote("")
      }
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        event.stopImmediatePropagation()
        if (selected) setSelected(null)
        else setActive(false)
        setHovered(null)
      }
      if (!isOverlay(event.target) && ["Enter", " "].includes(event.key)) {
        event.preventDefault()
        event.stopImmediatePropagation()
      }
    }
    const update = () => refresh((value) => value + 1)
    document.addEventListener("pointermove", move, true)
    document.addEventListener("click", click, true)
    document.addEventListener("keydown", key, true)
    window.addEventListener("scroll", update, true)
    window.addEventListener("resize", update)
    return () => {
      document.removeEventListener("pointermove", move, true)
      document.removeEventListener("click", click, true)
      document.removeEventListener("keydown", key, true)
      window.removeEventListener("scroll", update, true)
      window.removeEventListener("resize", update)
    }
  }, [active, selected])

  const output = () =>
    `Page annotations\n\n${JSON.stringify(annotations, null, 2)}`
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(output())
      toast.success("Annotations copied")
    } catch {
      toast.error("Could not copy annotations. Check clipboard permissions.")
    }
  }
  const startThread = async () => {
    try {
      sessionStorage.setItem(ANNOTATION_DRAFT_KEY, output())
      await navigate({
        to: "/agents",
        search: { annotationDraft: crypto.randomUUID() },
      })
      setActive(false)
      setSelected(null)
    } catch {
      toast.error(
        "Could not open a new thread. Your annotations are still here."
      )
    }
  }
  if (!active || typeof document === "undefined") return null
  const rect = hovered?.isConnected ? hovered.getBoundingClientRect() : null
  return createPortal(
    <div
      data-page-annotations=""
      className="pointer-events-none fixed inset-0 z-[2147483647]"
    >
      <div className="absolute inset-0 border-2 border-violet-500" />
      {rect && (
        <div
          className="absolute border-2 border-violet-500 bg-violet-500/15"
          style={{
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }}
        />
      )}
      <section
        aria-label="Page annotations"
        className="pointer-events-auto absolute bottom-6 left-1/2 flex max-h-[75vh] w-[min(440px,calc(100vw-32px))] -translate-x-1/2 flex-col gap-3 overflow-y-auto rounded-xl border bg-background p-4 text-foreground shadow-xl"
      >
        <div className="flex items-center justify-between">
          <strong>Annotation mode</strong>
          <Button variant="ghost" size="sm" onClick={toggle}>
            Close
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          ⌘ / Ctrl + Alt + Shift + A · Click an element to add a note. Esc
          cancels. Scroll to explore.
        </p>
        {selected ? (
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              if (!note.trim()) return
              setAnnotations((items) => [
                ...items,
                { context: selected, note: note.trim() },
              ])
              setSelected(null)
              setNote("")
            }}
          >
            <code className="truncate text-xs" title={selected.selector}>
              {selected.selector}
            </code>
            <textarea
              aria-label="Annotation note"
              autoFocus
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="What should change here?"
              className="min-h-20 rounded-md border bg-background p-2 text-sm"
            />
            <div className="flex gap-2">
              <Button type="submit" disabled={!note.trim()}>
                Save annotation
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setSelected(null)}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <p className="text-sm">
            {annotations.length
              ? `${annotations.length} annotation${annotations.length === 1 ? "" : "s"} ready`
              : "Select anything on this page to begin."}
          </p>
        )}
        {annotations.map((annotation, index) => (
          <div
            key={index}
            className="flex items-start gap-2 rounded-md border p-2 text-sm"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs text-muted-foreground">
                {index + 1}. {annotation.context.tag} ·{" "}
                {annotation.context.text || annotation.context.selector}
              </span>
              {annotation.note}
            </span>
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Remove annotation ${index + 1}`}
              onClick={() =>
                setAnnotations((items) => items.filter((_, i) => i !== index))
              }
            >
              Remove
            </Button>
          </div>
        ))}
        <p className="text-xs text-muted-foreground">
          Includes page URL, selector, classes, element text and React component
          names when available. Review notes before sharing; visible page text
          may be sensitive.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={!annotations.length || Boolean(selected)}
            onClick={copy}
          >
            Copy annotations
          </Button>
          <Button
            disabled={!annotations.length || Boolean(selected)}
            onClick={startThread}
          >
            Start new thread
          </Button>
        </div>
      </section>
    </div>,
    document.body
  )
}
