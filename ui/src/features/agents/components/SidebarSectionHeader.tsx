import {
  CaretDownIcon,
  CaretRightIcon,
  DotsThreeIcon,
} from "@phosphor-icons/react"
import type { ReactNode } from "react"

import { Menu, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import { cn } from "@/lib/utils"

/**
 * The Pinned / Repositories / Recents header. The caret only shows on hover while
 * the section is open — collapsed sections keep it visible, since that is the
 * only cue left once their contents are gone.
 */
export function SidebarSectionHeader({
  label,
  collapsed = false,
  onToggleCollapsed,
  menu,
  action,
}: {
  label: string
  collapsed?: boolean
  onToggleCollapsed?: () => void
  menu?: ReactNode
  action?: ReactNode
}) {
  const Caret = collapsed ? CaretRightIcon : CaretDownIcon
  const Heading = onToggleCollapsed ? "button" : "div"

  return (
    <div className="group/section flex items-center gap-1 pr-1 pl-2">
      <Heading
        type={onToggleCollapsed ? "button" : undefined}
        onClick={onToggleCollapsed}
        aria-expanded={onToggleCollapsed ? !collapsed : undefined}
        className="flex min-w-0 flex-1 items-center gap-1 py-1 text-left text-[13px] font-medium text-tertiary transition-colors hover:text-primary"
      >
        <span className="min-w-0 truncate">{label}</span>
        <Caret
          className={cn(
            "size-3.5 shrink-0",
            !onToggleCollapsed
              ? "hidden"
              : collapsed
                ? "block"
                : "hidden group-hover/section:block"
          )}
        />
      </Heading>
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
    <Menu>
      <MenuTrigger
        aria-label={label}
        title={label}
        className="flex size-5 items-center justify-center rounded text-tertiary opacity-0 transition-opacity group-hover/section:opacity-100 hover:bg-surface-level-2-hover hover:text-primary data-popup-open:opacity-100"
      >
        <DotsThreeIcon className="size-4" />
      </MenuTrigger>
      <MenuPopup align="start" className="w-56" sideOffset={4}>
        {children}
      </MenuPopup>
    </Menu>
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
      className="flex size-5 items-center justify-center rounded text-tertiary opacity-0 transition-opacity group-hover/section:opacity-100 hover:bg-surface-level-2-hover hover:text-primary"
    >
      {icon}
    </button>
  )
}
