import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

export function TableScroll({
  children,
  className,
  viewportClassName,
}: {
  children: ReactNode
  className?: string
  viewportClassName?: string
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const update = () => {
      const left = element.scrollLeft > 1
      const right =
        element.scrollWidth - element.clientWidth - element.scrollLeft > 1
      setEdges((current) =>
        current.left === left && current.right === right
          ? current
          : { left, right }
      )
    }
    const observer = new ResizeObserver(update)
    observer.observe(element)
    for (const child of element.children) observer.observe(child)
    element.addEventListener("scroll", update, { passive: true })
    update()
    return () => {
      observer.disconnect()
      element.removeEventListener("scroll", update)
    }
  }, [])

  return (
    <div className={cn("relative min-w-0", className)}>
      <div
        ref={viewport}
        role="region"
        aria-label="Scrollable table"
        tabIndex={edges.left || edges.right ? 0 : undefined}
        className={cn(
          "[scrollbar-width:thin] overflow-auto rounded-[inherit] focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none focus-visible:ring-inset",
          viewportClassName
        )}
      >
        {children}
      </div>
      {edges.left && (
        <div
          aria-hidden="true"
          data-table-scroll-edge="left"
          className="pointer-events-none absolute inset-y-0 left-0 w-4 rounded-l-[inherit] bg-gradient-to-r from-border-default/60 to-transparent"
        />
      )}
      {edges.right && (
        <div
          aria-hidden="true"
          data-table-scroll-edge="right"
          className="pointer-events-none absolute inset-y-0 right-0 w-4 rounded-r-[inherit] bg-gradient-to-l from-border-default/60 to-transparent"
        />
      )}
    </div>
  )
}
