import {
  CaretDownIcon,
  CaretRightIcon,
  DotsThreeIcon,
} from "@phosphor-icons/react"
import type { ReactNode } from "react"

import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { cn } from "@/lib/utils"

/**
 * The Pinned / Repositories / Recents header. The caret only shows on hover while
 * the section is open — collapsed sections keep it visible, since that is the
 * only cue left once their contents are gone.
 */
export function SidebarSectionHeader({
  label,
  collapsed,
  onToggleCollapsed,
  menu,
  action,
}: {
  label: string
  collapsed: boolean
  onToggleCollapsed: () => void
  menu?: ReactNode
  action?: ReactNode
}) {
  const Caret = collapsed ? CaretRightIcon : CaretDownIcon

  return (
    <div className="group/section flex items-center gap-1 pr-1 pl-2">
      <button
        type="button"
        onClick={onToggleCollapsed}
        aria-expanded={!collapsed}
        className="flex min-w-0 flex-1 items-center gap-1 py-1 text-left text-label font-medium text-ink-subtle/70 transition-colors hover:text-ink"
      >
        <span className="min-w-0 truncate">{label}</span>
        <Caret
          className={cn(
            "size-3.5 shrink-0",
            collapsed ? "block" : "hidden group-hover/section:block"
          )}
        />
      </button>
      <span className="flex shrink-0 items-center gap-0.5">
        {menu}
        {action}
      </span>
    </div>
  )
}

export function SidebarSectionMenu({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={label}
        title={label}
        className="flex size-5 items-center justify-center rounded-tick text-ink-subtle/70 opacity-0 transition-opacity group-hover/section:opacity-100 hover:bg-hover hover:text-ink data-popup-open:opacity-100"
      >
        <DotsThreeIcon className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56" sideOffset={4}>
        {children}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function SidebarSectionAction({
  label,
  icon,
  onClick,
}: {
  label: string
  icon: ReactNode
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex size-5 items-center justify-center rounded-tick text-ink-subtle/70 opacity-0 transition-opacity group-hover/section:opacity-100 hover:bg-hover hover:text-ink"
    >
      {icon}
    </button>
  )
}
