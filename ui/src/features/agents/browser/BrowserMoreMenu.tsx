import { Minus, MoreVertical, Plus, RotateCcw } from "lucide-react"

import type { BrowserTabController } from "@/features/agents/browser/browserController"
import type {
  BrowserColorScheme,
  BrowserTabState,
} from "@/features/agents/browser/browserTabStore"
import { Button } from "@/components/ui/button"
import {
  Menu,
  MenuItem,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  MenuSub,
  MenuSubPopup,
  MenuSubTrigger,
  MenuTrigger,
} from "@/components/ui/menu"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"

const COLOR_SCHEME_OPTIONS: ReadonlyArray<{
  value: BrowserColorScheme
  label: string
}> = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
]

interface Props {
  tab: BrowserTabState | null
  controller: BrowserTabController | null
  onToggleDeviceToolbar: () => void
}

/** Hard reload, DevTools, device toolbar, appearance, and zoom. */
export function BrowserMoreMenu({
  tab,
  controller,
  onToggleDeviceToolbar,
}: Props) {
  const disabled = !tab || !controller || !tab.attached
  const call =
    (
      operation:
        | ((controller: BrowserTabController) => Promise<void>)
        | undefined
    ) =>
    () => {
      if (!controller || !operation) return
      void operation(controller).catch(() => undefined)
    }
  const zoomLabel = `${Math.round((tab?.zoomFactor ?? 1) * 100)}%`
  const deviceToolbarVisible = tab !== null && tab.viewport.mode !== "fill"

  return (
    <Menu>
      <Tooltip>
        <TooltipTrigger
          render={
            <MenuTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  type="button"
                  aria-label="Browser menu"
                />
              }
            />
          }
        >
          <MoreVertical />
        </TooltipTrigger>
        <TooltipPopup>More</TooltipPopup>
      </Tooltip>
      <MenuPopup align="end" sideOffset={6} className="min-w-48">
        <MenuItem onClick={call((c) => c.hardReload())} disabled={disabled}>
          Hard reload
        </MenuItem>
        {controller?.openDevTools ? (
          <MenuItem
            onClick={call((c) => c.openDevTools?.() ?? Promise.resolve())}
            disabled={disabled}
          >
            Open DevTools
          </MenuItem>
        ) : null}
        <MenuItem
          onClick={onToggleDeviceToolbar}
          disabled={!tab || !controller}
        >
          {deviceToolbarVisible ? "Hide device toolbar" : "Show device toolbar"}
        </MenuItem>
        <MenuSub>
          <MenuSubTrigger disabled={disabled}>Appearance</MenuSubTrigger>
          <MenuSubPopup>
            <MenuRadioGroup
              value={tab?.colorScheme ?? "system"}
              onValueChange={(value) => {
                const scheme = COLOR_SCHEME_OPTIONS.find(
                  (option) => option.value === value
                )
                if (!controller || !scheme) return
                void controller
                  .setColorScheme(scheme.value)
                  .catch(() => undefined)
              }}
            >
              {COLOR_SCHEME_OPTIONS.map((option) => (
                <MenuRadioItem key={option.value} value={option.value}>
                  {option.label}
                </MenuRadioItem>
              ))}
            </MenuRadioGroup>
          </MenuSubPopup>
        </MenuSub>
        <MenuSeparator />
        <MenuItem
          closeOnClick={false}
          onClick={(event) => event.preventDefault()}
          className="justify-between"
          disabled={disabled}
        >
          <span>Zoom</span>
          <span className="flex items-center gap-1">
            <Button
              variant="outline"
              size="icon-xs"
              type="button"
              onClick={call((c) => c.zoomOut())}
              aria-label="Zoom out"
              disabled={disabled}
            >
              <Minus />
            </Button>
            <span className="min-w-12 text-center text-xs text-muted-foreground tabular-nums">
              {zoomLabel}
            </span>
            <Button
              variant="outline"
              size="icon-xs"
              type="button"
              onClick={call((c) => c.zoomIn())}
              aria-label="Zoom in"
              disabled={disabled}
            >
              <Plus />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              type="button"
              onClick={call((c) => c.resetZoom())}
              aria-label="Reset zoom"
              disabled={disabled}
            >
              <RotateCcw />
            </Button>
          </span>
        </MenuItem>
      </MenuPopup>
    </Menu>
  )
}
