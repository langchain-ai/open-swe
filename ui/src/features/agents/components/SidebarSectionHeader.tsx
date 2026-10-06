import type { ReactNode } from "react"
import { SidebarTreeGroup } from "@langchain/gtm-platform-design-system/patterns/sidebar-tree"
import { Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import type { Glyph } from "@/components/glyphs"
import { Ellipsis } from "@/components/glyphs"

/** Heading controls stay out of the way until the heading is pointed at. */
const REVEAL_ON_HEADING_CLASS =
  "opacity-0 transition-opacity duration-fast ease-out-quint group-hover/sidebar-heading:opacity-100 group-focus-within/sidebar-heading:opacity-100 data-popup-open:opacity-100 pointer-coarse:opacity-100 motion-reduce:transition-none"

/**
 * A category disclosure in the thread rail (Pinned, Repositories, Recents):
 * its button is named by the label alone and its rows sit inside the group.
 */
export function SidebarSection({
  label,
  collapsed,
  onToggleCollapsed,
  menu,
  action,
  children,
}: {
  label: string
  collapsed: boolean
  onToggleCollapsed: () => void
  menu?: ReactNode
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <SidebarTreeGroup
      label={label}
      kind="section"
      open={!collapsed}
      onOpenChange={(open) => {
        if (open === collapsed) onToggleCollapsed()
      }}
      trailing={
        menu || action ? (
          <>
            {menu}
            {action}
          </>
        ) : undefined
      }
    >
      <Stack gap="none" className="gap-0.5">
        {children}
      </Stack>
    </SidebarTreeGroup>
  )
}

/** The section's one options menu: grouping, ordering and what to show. */
export function SidebarSectionMenu({
  label,
  icon = Ellipsis,
  reveal = true,
  children,
}: {
  label: string
  icon?: Glyph
  /** Hidden until the heading is hovered; the list's own view menu stays put. */
  reveal?: boolean
  children: ReactNode
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            title={label}
            className={cn("text-ink-subtle", reveal && REVEAL_ON_HEADING_CLASS)}
          />
        }
      >
        <Icon icon={icon} size="sm" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side="right"
        align="start"
        sideOffset={6}
        className="w-56"
      >
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function SidebarSectionAction({
  label,
  icon,
  onClick,
  reveal = true,
}: {
  label: string
  icon: Glyph
  onClick: () => void
  reveal?: boolean
}) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn("text-ink-subtle", reveal && REVEAL_ON_HEADING_CLASS)}
    >
      <Icon icon={icon} size="sm" />
    </Button>
  )
}

export { REVEAL_ON_HEADING_CLASS }
