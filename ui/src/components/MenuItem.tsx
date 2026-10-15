// oxlint-disable-next-line no-restricted-imports -- this module is the sanctioned wrapper
import * as MacawContextMenu from "@langchain/macaw-components/ContextMenu"
// oxlint-disable-next-line no-restricted-imports -- this module is the sanctioned wrapper
import * as MacawDropdownMenu from "@langchain/macaw-components/DropdownMenu"
import { forwardRef, type ComponentProps, type ElementRef } from "react"

import { cn } from "@/lib/utils"

/** Macaw menu rows ship without a gap, so every leading icon would touch its label. */
const ROW_GAP = "gap-space-2"

export const DropdownMenuItem = forwardRef<
  ElementRef<typeof MacawDropdownMenu.DropdownMenuItem>,
  ComponentProps<typeof MacawDropdownMenu.DropdownMenuItem>
>(({ className, ...props }, ref) => (
  <MacawDropdownMenu.DropdownMenuItem
    ref={ref}
    className={cn(ROW_GAP, className)}
    {...props}
  />
))
DropdownMenuItem.displayName = "DropdownMenuItem"

export const DropdownMenuSubTrigger = forwardRef<
  ElementRef<typeof MacawDropdownMenu.DropdownMenuSubTrigger>,
  ComponentProps<typeof MacawDropdownMenu.DropdownMenuSubTrigger>
>(({ className, ...props }, ref) => (
  <MacawDropdownMenu.DropdownMenuSubTrigger
    ref={ref}
    className={cn(ROW_GAP, className)}
    {...props}
  />
))
DropdownMenuSubTrigger.displayName = "DropdownMenuSubTrigger"

export const ContextMenuItem = forwardRef<
  ElementRef<typeof MacawContextMenu.ContextMenuItem>,
  ComponentProps<typeof MacawContextMenu.ContextMenuItem>
>(({ className, ...props }, ref) => (
  <MacawContextMenu.ContextMenuItem
    ref={ref}
    className={cn(ROW_GAP, className)}
    {...props}
  />
))
ContextMenuItem.displayName = "ContextMenuItem"
