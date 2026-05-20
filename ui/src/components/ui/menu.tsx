"use client";

import { Menu as MenuPrimitive } from "@base-ui/react/menu";

import { cn } from "@/lib/utils";

const Menu = MenuPrimitive.Root;
const MenuTrigger = MenuPrimitive.Trigger;
const MenuPortal = MenuPrimitive.Portal;

function MenuContent({
  className,
  sideOffset = 6,
  align = "start",
  ...props
}: MenuPrimitive.Popup.Props & {
  sideOffset?: number;
  align?: MenuPrimitive.Positioner.Props["align"];
}) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Positioner sideOffset={sideOffset} align={align}>
        <MenuPrimitive.Popup
          data-slot="menu-content"
          className={cn(
            "z-50 min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-xs/relaxed text-popover-foreground shadow-md outline-none",
            className,
          )}
          {...props}
        />
      </MenuPrimitive.Positioner>
    </MenuPrimitive.Portal>
  );
}

function MenuItem({ className, ...props }: MenuPrimitive.Item.Props) {
  return (
    <MenuPrimitive.Item
      data-slot="menu-item"
      className={cn(
        "relative flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-xs/relaxed outline-none",
        "data-[highlighted]:bg-muted data-[highlighted]:text-foreground",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

function MenuSeparator({ className }: { className?: string }) {
  return <div className={cn("-mx-1 my-1 h-px bg-border", className)} />;
}

export { Menu, MenuTrigger, MenuContent, MenuItem, MenuPortal, MenuSeparator };
