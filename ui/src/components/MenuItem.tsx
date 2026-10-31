// oxlint-disable-next-line no-restricted-imports -- this module is the sanctioned wrapper
import * as MacawContextMenu from "@langchain/macaw-components/ContextMenu"
// oxlint-disable-next-line no-restricted-imports -- this module is the sanctioned wrapper
import * as MacawDropdownMenu from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { LinkSimpleIcon } from "@phosphor-icons/react/dist/ssr/LinkSimple"
import {
  forwardRef,
  type ComponentProps,
  type ElementRef,
  type ReactNode,
  useRef,
} from "react"

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

/**
 * A menu row that opens a link, with a trailing button that copies it instead.
 * With `href` the row is a real link (middle-click and open-in-new-tab work);
 * `onOpen` replaces a plain click, e.g. for router navigation. The copy button
 * swallows its own click; its pointerdown must still reach the row, or Radix
 * selects the row on pointerup.
 */
export function LinkMenuItem({
  item: Item,
  icon,
  label,
  copyLabel,
  href,
  external = false,
  onOpen,
  onCopy,
  title,
}: {
  item: typeof DropdownMenuItem | typeof ContextMenuItem
  icon: ReactNode
  label: ReactNode
  copyLabel: string
  href?: string
  external?: boolean
  onOpen?: () => void
  onCopy: () => void
  title?: string
}) {
  const anchor = useRef<HTMLAnchorElement>(null)
  // A click on the link itself already opened it; its select must not open it twice.
  const openedByLink = useRef(false)
  return (
    <Item
      title={title}
      className="pr-space-1"
      onSelect={() => {
        if (openedByLink.current) openedByLink.current = false
        else if (href) anchor.current?.click()
        else onOpen?.()
      }}
    >
      {href ? (
        <a
          ref={anchor}
          href={href}
          target={external ? "_blank" : undefined}
          rel={external ? "noreferrer" : undefined}
          className="flex min-w-0 flex-1 items-center gap-space-2 truncate"
          onClick={(event) => {
            openedByLink.current = true
            const plain =
              event.button === 0 &&
              !event.metaKey &&
              !event.ctrlKey &&
              !event.shiftKey &&
              !event.altKey
            if (onOpen && plain) {
              event.preventDefault()
              onOpen()
            }
          }}
        >
          {icon}
          {label}
        </a>
      ) : (
        <>
          {icon}
          <span className="min-w-0 flex-1 truncate">{label}</span>
        </>
      )}
      <IconButton
        icon={LinkSimpleIcon}
        label={copyLabel}
        tooltipProps={{ title: copyLabel }}
        size="xs"
        color="secondary"
        variant="plain"
        className="-my-space-1 shrink-0"
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          onCopy()
        }}
      />
    </Item>
  )
}
