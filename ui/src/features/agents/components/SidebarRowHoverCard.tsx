import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@langchain/macaw-components/HoverCard"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react"

import { useThreadDragging } from "./ThreadDragDrop.utils"

const HOVER_TRIANGLE_EDGE_OFFSET = 1
const HOVER_TRIANGLE_BASE_PADDING = 15

const RowHoverCardContext = createContext<{
  activeCardId: string | null
  dragging: boolean
  onOpenChange: (id: string, open: boolean) => void
}>({
  activeCardId: null,
  dragging: false,
  onOpenChange: (_id: string, _open: boolean) => {},
})

export function SidebarRowHoverProvider(props: { children: ReactNode }) {
  const [value, onChange] = useState<string | null>(null)
  const dragging = useThreadDragging(() => onChange(null))
  const onOpenChange = useCallback(
    (id: string, open: boolean) => {
      onChange((current) =>
        open && !dragging ? id : current === id ? null : current
      )
    },
    [dragging]
  )
  const hoverContext = useMemo(
    () => ({
      activeCardId: dragging ? null : value,
      dragging,
      onOpenChange,
    }),
    [value, dragging, onOpenChange]
  )

  return (
    <RowHoverCardContext value={hoverContext}>
      {props.children}
    </RowHoverCardContext>
  )
}

/** Rich preview beside a row; only one sidebar preview is open at a time. */
export function SidebarRowHoverCard({
  trigger,
  card,
  className,
  openDelay = 500,
  openOnEnter,
}: {
  trigger: ReactElement
  card: ReactNode
  className: string
  openDelay?: number
  openOnEnter?: (row: HTMLElement) => boolean
}) {
  const id = useId()
  const { activeCardId, dragging, onOpenChange } =
    useContext(RowHoverCardContext)
  const cardRef = useRef<HTMLDivElement>(null)
  const safeAreaRef = useRef<SVGSVGElement>(null)
  const triangleRef = useRef<SVGPolygonElement>(null)
  const dismissedUntilPointerLeave = useRef(false)

  useEffect(() => () => onOpenChange(id, false), [id, onOpenChange])

  useEffect(() => {
    if (activeCardId !== id) return
    const abort = new AbortController()
    const dismiss = () => onOpenChange(id, false)
    document.addEventListener("scroll", dismiss, {
      capture: true,
      signal: abort.signal,
    })
    window.addEventListener("resize", dismiss, { signal: abort.signal })
    window.addEventListener("blur", dismiss, { signal: abort.signal })
    return () => abort.abort()
  }, [activeCardId, id, onOpenChange])

  const onPointerLeave = (event: React.PointerEvent) => {
    dismissedUntilPointerLeave.current = false
    if (activeCardId !== id || event.pointerType === "touch") return

    const cardNode = cardRef.current?.getBoundingClientRect()
    const svgNode = safeAreaRef.current?.getBoundingClientRect()
    if (!cardNode || !svgNode) return

    const row = event.currentTarget.getBoundingClientRect()

    let points: string
    if (cardNode.left >= row.right || cardNode.right <= row.left) {
      const offset =
        cardNode.left >= row.right
          ? HOVER_TRIANGLE_EDGE_OFFSET
          : -HOVER_TRIANGLE_EDGE_OFFSET
      const edge = (offset > 0 ? cardNode.left : cardNode.right) + offset
      points = `${event.clientX - offset - svgNode.left},${event.clientY - svgNode.top} ${edge - svgNode.left},${cardNode.top - HOVER_TRIANGLE_BASE_PADDING - svgNode.top} ${edge - svgNode.left},${cardNode.bottom + HOVER_TRIANGLE_BASE_PADDING - svgNode.top}`
    } else {
      const offset =
        cardNode.top >= row.bottom
          ? HOVER_TRIANGLE_EDGE_OFFSET
          : -HOVER_TRIANGLE_EDGE_OFFSET
      const edge = (offset > 0 ? cardNode.top : cardNode.bottom) + offset
      points = `${event.clientX - svgNode.left},${event.clientY - offset - svgNode.top} ${cardNode.left - HOVER_TRIANGLE_BASE_PADDING - svgNode.left},${edge - svgNode.top} ${cardNode.right + HOVER_TRIANGLE_BASE_PADDING - svgNode.left},${edge - svgNode.top}`
    }
    triangleRef.current?.setAttribute("points", points)
  }

  return (
    <HoverCard
      open={!dragging && activeCardId === id}
      openDelay={activeCardId === null ? openDelay : 0}
      closeDelay={100}
      onOpenChange={(open) =>
        onOpenChange(id, open && !dismissedUntilPointerLeave.current)
      }
    >
      <HoverCardTrigger
        asChild
        data-hover-preview={activeCardId === id ? "open" : "closed"}
        onClickCapture={(event) => {
          dismissedUntilPointerLeave.current = event.detail > 0
          onOpenChange(id, false)
        }}
        onPointerEnter={(event) => {
          if (
            dragging ||
            dismissedUntilPointerLeave.current ||
            event.pointerType === "touch"
          )
            return
          if (openOnEnter?.(event.currentTarget)) onOpenChange(id, true)
        }}
        onPointerLeave={onPointerLeave}
      >
        {trigger}
      </HoverCardTrigger>
      <HoverCardContent
        ref={cardRef}
        side="right"
        align="start"
        sideOffset={8}
        className={className}
      >
        {card}
        <svg
          ref={safeAreaRef}
          aria-hidden="true"
          data-hover-safe-area
          className="text-brand pointer-events-none absolute inset-0 h-full w-full overflow-visible"
        >
          <polygon
            ref={triangleRef}
            className="pointer-events-auto fill-transparent"
          />
        </svg>
      </HoverCardContent>
    </HoverCard>
  )
}
