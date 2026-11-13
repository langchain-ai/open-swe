import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react"

export const SidebarThreadDragPreviewContext = createContext<
  (id: string, content: ReactNode) => void
>(() => {})

export const ThreadDragCountContext = createContext(0)

export interface ThreadDragItem {
  id: string
  row: HTMLElement
  content: ReactNode
  pinned: boolean
  archived: boolean
}

export function useThreadDragPreviewContent(id: string, content: ReactNode) {
  const register = useContext(SidebarThreadDragPreviewContext)

  useEffect(() => {
    register(id, content)
    return () => register(id, null)
  }, [id, content, register])
}

export function useThreadDragging(onStart: () => void) {
  const started = useEffectEvent(onStart)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const controller = new AbortController()

    const signal = controller.signal
    document.addEventListener(
      "dragstart",
      (event) => {
        if (event.defaultPrevented) return
        setDragging(true)
        started()
      },
      { signal }
    )

    document.addEventListener(
      "pointermove",
      (event) => {
        if (event.buttons !== 0) return
        setDragging(false)
      },
      { capture: true, signal }
    )

    document.addEventListener("dragend", () => setDragging(false), {
      capture: true,
      signal,
    })

    document.addEventListener("drop", () => setDragging(false), {
      capture: true,
      signal,
    })

    return () => controller.abort()
  }, [])

  return dragging
}

export function useThreadDragAnimation({
  row,
  y,
  items,
  container,
  onFinish,
}: {
  row: HTMLElement
  y: number
  items: Array<ThreadDragItem>
  container: HTMLElement
  onFinish: () => void
}) {
  const preview = useRef<HTMLDivElement>(null)
  const layers = useRef(new Map<string, HTMLDivElement>())
  const finishCallback = useEffectEvent(onFinish)

  useEffect(() => {
    const node = preview.current
    if (!node) return
    const bounds = row.getBoundingClientRect()
    const overlayBounds = container.getBoundingClientRect()

    const duration = (() => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        return 0
      }
      return 200
    })()

    const sources = items.map((item, index) => {
      const source =
        item.row.closest<HTMLElement>("[data-sidebar-thread-row]") ?? item.row
      const opacity = source.style.opacity
      const layer = layers.current.get(item.id)
      const itemBounds = item.row.getBoundingClientRect()

      source.style.opacity = "0"

      const depth = (() => {
        if (items.length <= 1) return 0
        return (items.length - 1 - index) / (items.length - 1)
      })()

      // All offsets share 1/4 a row of space, regardless of selection size, compute the Y-offset from there
      const offset = depth * bounds.height * 0.25
      const scale = 1 - depth * 0.1
      const layerOpacity = 1 - depth * 0.5

      const transform = `translateY(${offset}px) scale(${scale})`
      if (layer) {
        layer.style.width = `${itemBounds.width}px`
        layer.style.height = `${itemBounds.height}px`
        layer.style.transform = transform
        layer.style.opacity = String(layerOpacity)
      }

      const animation = (() => {
        if (!layer || item.row === row) return undefined
        return layer.animate(
          [
            {
              transform: `translate(${itemBounds.left - bounds.left}px, ${itemBounds.top - bounds.top}px) scale(1)`,
              opacity: 1,
            },
            { transform, opacity: layerOpacity },
          ],
          { duration, easing: "ease-out" }
        )
      })()

      return { item, source, opacity, layer, animation }
    })

    const offsetY = y - bounds.top
    const left = bounds.left - overlayBounds.left

    let top = bounds.top - overlayBounds.top
    let returning = false
    let animation: Animation | undefined

    const returns: Array<Animation> = []
    const controller = new AbortController()

    const move = (event: DragEvent) => {
      if (returning) return
      // Accept background drops so the browser does not animate a rejected drop.
      event.preventDefault()
      top = event.clientY - offsetY - overlayBounds.top
      node.style.transform = `translate(${left}px, ${top}px)`
    }

    const finish = () => {
      if (returning) return
      returning = true

      const destination = row.getBoundingClientRect()
      for (const entry of sources) {
        if (!entry.layer) continue
        const appearance = getComputedStyle(entry.layer)
        const transform = appearance.transform
        const opacity = appearance.opacity
        entry.animation?.cancel()
        const target = entry.item.row.getBoundingClientRect()
        const targetTransform = `translate(${target.left - bounds.left}px, ${target.top - destination.top}px) scale(1)`
        entry.layer.style.transform = targetTransform
        entry.layer.style.opacity = "1"
        returns.push(
          entry.layer.animate(
            [
              { transform, opacity },
              { transform: targetTransform, opacity: 1 },
            ],
            { duration, easing: "ease-out" }
          )
        )
      }
      animation = node.animate(
        [
          { transform: `translate(${left}px, ${top}px)` },
          {
            transform: `translate(${left}px, ${destination.top - overlayBounds.top}px)`,
          },
        ],
        { duration, easing: "ease-out", fill: "forwards" }
      )

      animation.onfinish = () => finishCallback()
    }

    const drop = (event: DragEvent) => {
      if (event.defaultPrevented) {
        returning = true
        finishCallback()
        return
      }
      event.preventDefault()
      finish()
    }

    node.style.width = `${bounds.width}px`
    node.style.height = `${bounds.height}px`
    node.style.transform = `translate(${left}px, ${top}px)`

    document.addEventListener("dragover", move, {
      capture: true,
      signal: controller.signal,
    })

    // Bubble after row and action handlers have processed successful drops.
    document.addEventListener("drop", drop, { signal: controller.signal })
    document.addEventListener("dragend", finish, {
      capture: true,
      signal: controller.signal,
    })

    return () => {
      controller.abort()
      animation?.cancel()

      for (const entry of sources) {
        entry.animation?.cancel()
        entry.source.style.opacity = entry.opacity
      }
      for (const entry of returns) entry.cancel()
    }
  }, [row, y, items, container])

  return { preview, layers }
}
