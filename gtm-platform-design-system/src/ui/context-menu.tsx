"use client";

import { ContextMenu as ContextMenuPrimitive } from "@base-ui/react/context-menu";

// Context menus share Base UI's menu popup and items, including their styling
// and keyboard behavior. Only the right-click / long-press trigger differs.
export {
  DropdownMenuContent as ContextMenuContent,
  DropdownMenuItem as ContextMenuItem,
  DropdownMenuCheckboxItem as ContextMenuCheckboxItem,
} from "./dropdown-menu";

export function ContextMenu(props: ContextMenuPrimitive.Root.Props) {
  return <ContextMenuPrimitive.Root {...props} />;
}

export function ContextMenuTrigger({ onKeyDown, ...props }: ContextMenuPrimitive.Trigger.Props) {
  return <ContextMenuPrimitive.Trigger {...props} onKeyDown={(event) => {
    onKeyDown?.(event);
    if (event.defaultPrevented || !(event.key === "ContextMenu" || (event.shiftKey && event.key === "F10"))) return;
    event.preventDefault();
    // macOS does not dispatch the native contextmenu event for Shift+F10.
    // Route keyboard activation through the same anchored Base UI trigger.
    const bounds = event.currentTarget.getBoundingClientRect();
    event.currentTarget.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: bounds.right,
      clientY: bounds.bottom,
    }));
  }} />;
}
