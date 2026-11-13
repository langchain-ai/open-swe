import { createPortal } from "react-dom"

import {
  ThreadDragCountContext,
  type ThreadDragItem,
  useThreadDragAnimation,
} from "@/features/agents/components/ThreadDragDrop.utils"

export function ThreadDragPreview(props: {
  row: HTMLElement
  y: number
  items: Array<ThreadDragItem>
  container: HTMLElement
  selected: boolean
  onFinish: () => void
}) {
  const { preview, layers } = useThreadDragAnimation(props)

  return createPortal(
    <div
      ref={preview}
      aria-hidden="true"
      data-selected={props.selected}
      className="group/drag-preview agents-ui pointer-events-none absolute top-0 left-0 z-drag-overlay"
    >
      {props.items.map((item) => (
        <div
          key={item.id}
          ref={(element) => {
            if (element) layers.current.set(item.id, element)
            else layers.current.delete(item.id)
          }}
          className="absolute top-0 left-0 origin-top"
        >
          <ThreadDragCountContext
            value={item === props.items.at(-1) ? props.items.length - 1 : 0}
          >
            {item.content}
          </ThreadDragCountContext>
        </div>
      ))}
    </div>,
    props.container
  )
}
