import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@langchain/gtm-platform-design-system/ui/cn"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"

import { PanelLeft } from "@/components/glyphs"
import { OpenSweMark, OpenSweMarkTile } from "@/components/rail/OpenSweMark"

const PRODUCT_NAME = "Open SWE"

/* Open brand lockup sits on the control rung; the rail's own pt-2 is its top air. */
const BRAND_CLASS = "h-control shrink-0 px-3"

/* The collapse control reports geometry through aria-expanded, not a popup. */
const COLLAPSE_BUTTON_CLASS = "aria-expanded:bg-transparent"

interface AppRailBrandProps {
  collapsed: boolean
  onToggleCollapsed: () => void
}

function isDesktopApp(): boolean {
  return typeof window !== "undefined" && Boolean(window.openSweDesktop)
}

/**
 * The macOS app draws its traffic lights over the rail's top-left corner, so
 * the desktop build reserves a drag strip above the brand row.
 */
export function DesktopRailInset() {
  if (!isDesktopApp()) return null
  return <Box aria-hidden data-desktop-drag-region="" className="h-8 shrink-0" />
}

/** Mark tile + product name + collapse. Collapsed, the mark is the expand control. */
export function AppRailBrand({ collapsed, onToggleCollapsed }: AppRailBrandProps) {
  const toggleLabel = collapsed ? "Expand sidebar" : "Collapse sidebar"
  return (
    <>
      <DesktopRailInset />
      <Inline
        data-slot="app-rail-brand"
        gap="sm"
        align="center"
        justify="between"
        className={cn(BRAND_CLASS, "relative overflow-hidden")}
      >
        <Inline
          gap="sm"
          align="center"
          aria-hidden={collapsed}
          className={cn(
            "min-w-0 pr-8 transition-opacity duration-fast ease-out-quint motion-reduce:transition-none",
            collapsed && "pointer-events-none opacity-0"
          )}
        >
          <OpenSweMarkTile />
          <Box
            render={<span />}
            className="min-w-0 truncate text-title font-semibold text-ink"
          >
            {PRODUCT_NAME}
          </Box>
        </Inline>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={toggleLabel}
                aria-expanded={!collapsed}
                data-sidebar-collapse=""
                onClick={onToggleCollapsed}
                className={cn("absolute right-2.5", COLLAPSE_BUTTON_CLASS)}
              >
                <OpenSweMark
                  className={cn(
                    "pointer-events-none absolute text-mark-ink transition-opacity duration-fast ease-out-quint motion-reduce:transition-none",
                    collapsed
                      ? "opacity-100 group-hover/button:opacity-0 group-focus-visible/button:opacity-0"
                      : "opacity-0"
                  )}
                />
                <Icon
                  icon={PanelLeft}
                  className={cn(
                    "text-ink-subtle transition-opacity duration-fast ease-out-quint motion-reduce:transition-none",
                    collapsed &&
                      "opacity-0 group-hover/button:opacity-100 group-focus-visible/button:opacity-100"
                  )}
                />
              </Button>
            }
          />
          <TooltipContent side={collapsed ? "right" : "bottom"}>
            {toggleLabel}
          </TooltipContent>
        </Tooltip>
      </Inline>
    </>
  )
}

/** The settings rail's masthead: the word Settings + collapse, no mark tile. */
export function AppRailSettingsBrand({
  collapsed,
  onToggleCollapsed,
}: AppRailBrandProps) {
  const toggleLabel = collapsed ? "Expand sidebar" : "Collapse sidebar"
  const toggle = (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={toggleLabel}
            aria-expanded={!collapsed}
            data-sidebar-collapse=""
            onClick={onToggleCollapsed}
            className={COLLAPSE_BUTTON_CLASS}
          >
            <Icon icon={PanelLeft} className="text-ink-subtle" />
          </Button>
        }
      />
      <TooltipContent side={collapsed ? "right" : "bottom"}>
        {toggleLabel}
      </TooltipContent>
    </Tooltip>
  )
  return (
    <>
      <DesktopRailInset />
      {collapsed ? (
        <Inline
          data-slot="app-rail-settings-brand"
          justify="center"
          align="center"
          className="h-control shrink-0 px-1"
        >
          {toggle}
        </Inline>
      ) : (
        <Inline
          data-slot="app-rail-settings-brand"
          gap="sm"
          align="center"
          justify="between"
          className={BRAND_CLASS}
        >
          <Box
            render={<span />}
            className="min-w-0 truncate text-title font-semibold text-ink"
          >
            Settings
          </Box>
          {toggle}
        </Inline>
      )}
    </>
  )
}
