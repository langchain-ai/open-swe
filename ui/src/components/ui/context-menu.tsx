import { ContextMenu as ContextMenuPrimitive } from "@base-ui/react/context-menu"

import { cn } from "@/lib/utils"

/** Items come from `@/components/ui/menu`; Base UI shares menu parts across both roots. */
const ContextMenu = ContextMenuPrimitive.Root

function ContextMenuTrigger(props: ContextMenuPrimitive.Trigger.Props) {
  return (
    <ContextMenuPrimitive.Trigger data-slot="context-menu-trigger" {...props} />
  )
}

function ContextMenuPopup({
  children,
  className,
  ...props
}: ContextMenuPrimitive.Popup.Props) {
  return (
    <ContextMenuPrimitive.Portal>
      <ContextMenuPrimitive.Positioner
        className="z-[60] outline-none"
        data-slot="context-menu-positioner"
      >
        <ContextMenuPrimitive.Popup
          className={cn(
            "dropdown-glass relative flex min-w-40 origin-(--transform-origin) rounded-lg transition-[scale,opacity] outline-none data-starting-style:scale-98 data-starting-style:opacity-0",
            className
          )}
          data-slot="context-menu-popup"
          {...props}
        >
          <div className="max-h-(--available-height) w-full overflow-y-auto p-1">
            {children}
          </div>
        </ContextMenuPrimitive.Popup>
      </ContextMenuPrimitive.Positioner>
    </ContextMenuPrimitive.Portal>
  )
}

export { ContextMenu, ContextMenuTrigger, ContextMenuPopup }
