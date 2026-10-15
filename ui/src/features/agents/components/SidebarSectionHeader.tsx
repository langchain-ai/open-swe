import {
  CaretDownIcon,
  CaretRightIcon,
} from "@langchain/macaw-components/icons"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

const SECTION_CONTROL =
  "text-tertiary opacity-0 transition-opacity group-hover/section:opacity-100 hover:text-primary focus-visible:opacity-100"

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
    <div className="group/section flex items-center gap-space-1 pr-space-1 pl-space-2">
      <Heading
        type={onToggleCollapsed ? "button" : undefined}
        onClick={onToggleCollapsed}
        aria-expanded={onToggleCollapsed ? !collapsed : undefined}
        className="flex min-w-0 flex-1 items-center gap-space-1 py-space-1 text-left text-xs font-medium text-tertiary transition-colors hover:text-primary"
      >
        <span className="min-w-0 truncate">{label}</span>
        <Caret
          size={14}
          weight="regular"
          className={cn(
            "shrink-0",
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

/** Children are Macaw `DropdownMenu*` items. */
export function SidebarSectionMenu({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconButton
          icon={DotsThreeIcon}
          label={label}
          size="xs"
          color="secondary"
          variant="plain"
          className={cn(SECTION_CONTROL, "data-[state=open]:opacity-100")}
        />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
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
  icon: IconComponent
  onClick: () => void
}) {
  return (
    <IconButton
      icon={icon}
      label={label}
      size="xs"
      color="secondary"
      variant="plain"
      onClick={onClick}
      className={SECTION_CONTROL}
    />
  )
}
