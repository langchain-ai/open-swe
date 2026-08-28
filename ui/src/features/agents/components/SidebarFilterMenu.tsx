import { Menu } from "@base-ui/react/menu"
import { CaretRightIcon, CheckIcon, FunnelIcon } from "@phosphor-icons/react"

import type { SidebarThreadLocation } from "@/features/agents/lib/sidebarThreads"
import type { AgentSource, AgentStatus } from "@/features/agents/lib/types"
import type {
  PrFilter,
  SidebarFacets,
  SidebarFilters,
} from "@/features/agents/lib/sidebarFilter"
import type { SidebarPrefs } from "@/features/agents/lib/sidebarPrefs"
import {
  LOCATION_FILTER_OPTIONS,
  PR_FILTER_OPTIONS,
  SOURCE_FILTER_OPTIONS,
  STATUS_FILTER_OPTIONS,
  hasActiveFilters,
  toggleArrayValue,
} from "@/features/agents/lib/sidebarFilter"
import { cn } from "@/lib/utils"

const POPUP_CLASS =
  "z-50 min-w-[12rem] origin-(--transform-origin) overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"

const ITEM_CLASS =
  "flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"

const SEPARATOR_CLASS = "my-1 h-px bg-border"

function CountBadge({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
      {count}
    </span>
  )
}

function CheckboxSubmenu({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string
  options: Array<{ value: string; label: string }>
  selected: Array<string>
  onToggle: (value: string) => void
}) {
  const disabled = options.length === 0
  return (
    <Menu.SubmenuRoot>
      <Menu.SubmenuTrigger className={ITEM_CLASS} disabled={disabled}>
        <span className="truncate">{label}</span>
        {selected.length > 0 ? (
          <CountBadge count={selected.length} />
        ) : (
          <CaretRightIcon className="ml-auto size-3.5 shrink-0" />
        )}
      </Menu.SubmenuTrigger>
      <Menu.Portal>
        <Menu.Positioner
          align="start"
          sideOffset={4}
          className="z-50 outline-none"
        >
          <Menu.Popup className={cn(POPUP_CLASS, "max-h-72 overflow-y-auto")}>
            {options.map((option) => (
              <Menu.CheckboxItem
                key={option.value}
                checked={selected.includes(option.value)}
                onCheckedChange={() => onToggle(option.value)}
                closeOnClick={false}
                className={ITEM_CLASS}
              >
                <span className="truncate">{option.label}</span>
                <Menu.CheckboxItemIndicator className="ml-auto flex">
                  <CheckIcon className="size-3.5 shrink-0" weight="bold" />
                </Menu.CheckboxItemIndicator>
              </Menu.CheckboxItem>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.SubmenuRoot>
  )
}

export interface SidebarFilterMenuProps {
  prefs: SidebarPrefs
  facets: SidebarFacets
  onFiltersChange: (filters: SidebarFilters) => void
  onCompactChange: (compact: boolean) => void
  onResetFilters: () => void
}

export function SidebarFilterMenu({
  prefs,
  facets,
  onFiltersChange,
  onCompactChange,
  onResetFilters,
}: SidebarFilterMenuProps) {
  const { filters } = prefs
  const active = hasActiveFilters(filters)

  const patch = (next: Partial<SidebarFilters>) =>
    onFiltersChange({ ...filters, ...next })

  return (
    <Menu.Root>
      <Menu.Trigger
        render={
          <button
            type="button"
            aria-label="Filter threads"
            className="relative flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors outline-none hover:bg-sidebar-row-hover hover:text-foreground data-popup-open:bg-sidebar-row-hover data-popup-open:text-foreground"
          >
            <FunnelIcon className="size-4" />
            {active && (
              <span className="absolute top-1 right-1 size-1.5 rounded-full bg-primary" />
            )}
          </button>
        }
      />
      <Menu.Portal>
        <Menu.Positioner
          side="top"
          align="end"
          sideOffset={6}
          className="z-50 outline-none"
        >
          <Menu.Popup className={POPUP_CLASS}>
            <Menu.SubmenuRoot>
              <Menu.SubmenuTrigger className={ITEM_CLASS}>
                <span className="truncate">Filter</span>
                <CaretRightIcon className="ml-auto size-3.5 shrink-0" />
              </Menu.SubmenuTrigger>
              <Menu.Portal>
                <Menu.Positioner
                  align="start"
                  sideOffset={4}
                  className="z-50 outline-none"
                >
                  <Menu.Popup className={POPUP_CLASS}>
                    <CheckboxSubmenu
                      label="Status"
                      options={STATUS_FILTER_OPTIONS}
                      selected={filters.statuses}
                      onToggle={(value) =>
                        patch({
                          statuses: toggleArrayValue(
                            filters.statuses,
                            value as AgentStatus
                          ),
                        })
                      }
                    />
                    <CheckboxSubmenu
                      label="Source"
                      options={SOURCE_FILTER_OPTIONS}
                      selected={filters.sources}
                      onToggle={(value) =>
                        patch({
                          sources: toggleArrayValue(
                            filters.sources,
                            value as AgentSource
                          ),
                        })
                      }
                    />
                    <CheckboxSubmenu
                      label="Location"
                      options={LOCATION_FILTER_OPTIONS}
                      selected={filters.locations}
                      onToggle={(value) =>
                        patch({
                          locations: toggleArrayValue(
                            filters.locations,
                            value as SidebarThreadLocation
                          ),
                        })
                      }
                    />
                    <CheckboxSubmenu
                      label="Pull request"
                      options={PR_FILTER_OPTIONS}
                      selected={filters.pr}
                      onToggle={(value) =>
                        patch({
                          pr: toggleArrayValue(filters.pr, value as PrFilter),
                        })
                      }
                    />
                    <CheckboxSubmenu
                      label="Model"
                      options={facets.models.map((m) => ({
                        value: m,
                        label: m,
                      }))}
                      selected={filters.models}
                      onToggle={(value) =>
                        patch({
                          models: toggleArrayValue(filters.models, value),
                        })
                      }
                    />
                    <Menu.Separator className={SEPARATOR_CLASS} />

                    <Menu.CheckboxItem
                      checked={filters.includeAutomations}
                      onCheckedChange={(checked) =>
                        patch({ includeAutomations: checked })
                      }
                      closeOnClick={false}
                      className={ITEM_CLASS}
                    >
                      <span className="truncate">Include automations</span>
                      <Menu.CheckboxItemIndicator className="ml-auto flex">
                        <CheckIcon
                          className="size-3.5 shrink-0"
                          weight="bold"
                        />
                      </Menu.CheckboxItemIndicator>
                    </Menu.CheckboxItem>

                    <Menu.CheckboxItem
                      checked={filters.includeResolved}
                      onCheckedChange={(checked) =>
                        patch({ includeResolved: checked })
                      }
                      closeOnClick={false}
                      className={ITEM_CLASS}
                    >
                      <span className="truncate">Include resolved</span>
                      <Menu.CheckboxItemIndicator className="ml-auto flex">
                        <CheckIcon
                          className="size-3.5 shrink-0"
                          weight="bold"
                        />
                      </Menu.CheckboxItemIndicator>
                    </Menu.CheckboxItem>

                    <Menu.Separator className={SEPARATOR_CLASS} />

                    <Menu.Item
                      onClick={onResetFilters}
                      disabled={!active}
                      className={ITEM_CLASS}
                    >
                      Reset filters
                    </Menu.Item>
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.SubmenuRoot>

            <Menu.Separator className={SEPARATOR_CLASS} />

            <Menu.CheckboxItem
              checked={prefs.compact}
              onCheckedChange={(checked) => onCompactChange(checked)}
              closeOnClick={false}
              className={ITEM_CLASS}
            >
              <span className="truncate">Compact</span>
              <Menu.CheckboxItemIndicator className="ml-auto flex">
                <CheckIcon className="size-3.5 shrink-0" weight="bold" />
              </Menu.CheckboxItemIndicator>
            </Menu.CheckboxItem>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )
}
