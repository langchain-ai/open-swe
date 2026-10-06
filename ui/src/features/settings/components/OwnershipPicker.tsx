import { useId, useMemo, useState, type ReactNode } from "react"

import { EmptyState } from "@langchain/gtm-platform-design-system/patterns/empty-state"
import { FormField } from "@langchain/gtm-platform-design-system/patterns/form-field"
import { StateNotice } from "@langchain/gtm-platform-design-system/patterns/state-notice"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Checkbox } from "@langchain/gtm-platform-design-system/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@langchain/gtm-platform-design-system/ui/dialog"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Input } from "@langchain/gtm-platform-design-system/ui/input"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import { SearchInput } from "@langchain/gtm-platform-design-system/ui/search-input"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { Switch } from "@langchain/gtm-platform-design-system/ui/switch"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@langchain/gtm-platform-design-system/ui/toggle-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@langchain/gtm-platform-design-system/ui/tooltip"
import { AlertTriangle, Search } from "@/components/glyphs"
import { cn } from "@/lib/utils"

/** The segmented filter's value for "no category". */
const ALL_FILTER = "__all__"

export interface PickerOwner {
  slug: string
  name: string
}

/** One thing a workspace can own: a repository or a Slack channel. */
export interface PickerItem {
  id: string
  label: string
  meta?: string
  icon?: ReactNode
  /** The workspace that owns it today, when one does. */
  owner?: PickerOwner | null
  /** Shown under the row; a selected item with a warning still saves. */
  warning?: string
  disabled?: boolean
}

export interface PickerFilter {
  label: string
  matches: (item: PickerItem) => boolean
}

export interface ManualEntry {
  label: string
  placeholder: string
  hint?: string
  /** The canonical id for a typed value, or null when it cannot be one. */
  normalize: (raw: string) => string | null
  invalidHint: string
}

export interface OwnershipPickerProps {
  triggerLabel: string
  title: string
  description?: string
  noun: string
  pluralNoun: string
  items: Array<PickerItem>
  selected: Array<string>
  /** The workspace being edited; null while creating one. */
  workspaceSlug: string | null
  onChange: (ids: Array<string>) => void
  searchPlaceholder: string
  filter?: PickerFilter
  filters?: Array<PickerFilter>
  manual?: ManualEntry
  loading?: boolean
  actions?: ReactNode
  loadError?: string | null
  /** A caveat about the directory itself, shown above the rows. */
  notice?: string | null
  disabled?: boolean
}

function matchesSearch(item: PickerItem, search: string): boolean {
  const needle = search.trim().toLowerCase()
  if (!needle) return true
  return (
    item.label.toLowerCase().includes(needle) ||
    item.id.toLowerCase().includes(needle)
  )
}

/**
 * Picks the repositories or Slack channels a workspace owns.
 *
 * Rows are grouped by what saving would mean: already in this workspace,
 * available, or owned by another workspace (shown, but not selectable, since
 * each belongs to exactly one). A selected id the directory does not list
 * still renders, so a binding is never hidden from the admin who made it.
 */
export function OwnershipPicker({
  triggerLabel,
  title,
  description,
  noun,
  pluralNoun,
  items,
  selected,
  workspaceSlug,
  onChange,
  searchPlaceholder,
  filter,
  filters,
  manual,
  loading = false,
  actions,
  loadError = null,
  notice = null,
  disabled = false,
}: OwnershipPickerProps) {
  const rowIdPrefix = useId()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Array<string>>(selected)
  const [search, setSearch] = useState("")
  const [filterOn, setFilterOn] = useState(true)
  const [activeFilter, setActiveFilter] = useState<string | null>(null)
  const categoryFilter = filters?.find(
    (option) => option.label === activeFilter
  )
  const [extras, setExtras] = useState<Array<PickerItem>>([])
  const [manualValue, setManualValue] = useState("")
  const [manualError, setManualError] = useState<string | null>(null)

  const rows = useMemo(() => {
    const byId = new Map(items.map((item) => [item.id, item]))
    for (const extra of extras)
      if (!byId.has(extra.id)) byId.set(extra.id, extra)
    for (const id of draft) if (!byId.has(id)) byId.set(id, { id, label: id })
    return byId
  }, [items, extras, draft])

  const draftSet = new Set(draft)
  const ownedElsewhere = (item: PickerItem) =>
    !!item.owner && item.owner.slug !== workspaceSlug
  const filterActive = !!filter && filterOn
  const passesFilter = (item: PickerItem) =>
    (!filterActive || filter.matches(item)) &&
    (!categoryFilter || categoryFilter.matches(item))

  const inThis = draft
    .map((id) => rows.get(id))
    .filter((item): item is PickerItem => !!item)
  const rest = [...rows.values()].filter(
    (item) => !draftSet.has(item.id) && matchesSearch(item, search)
  )
  const available = rest.filter(
    (item) => !ownedElsewhere(item) && passesFilter(item)
  )
  const owned = rest.filter(
    (item) => ownedElsewhere(item) && passesFilter(item)
  )
  const hiddenCount = rest.filter((item) => !passesFilter(item)).length

  const toggle = (id: string) =>
    setDraft((current) =>
      current.includes(id)
        ? current.filter((entry) => entry !== id)
        : [...current, id]
    )

  const addManual = () => {
    if (!manual) return
    const id = manual.normalize(manualValue)
    if (!id) {
      setManualError(manual.invalidHint)
      return
    }
    // Typing the id of something another workspace owns must not get past
    // the disabled row it would otherwise have to click.
    const known = rows.get(id)
    if (known && ownedElsewhere(known) && known.owner) {
      setManualError(`${known.label} belongs to ${known.owner.name}.`)
      return
    }
    if (!known) setExtras((current) => [...current, { id, label: id }])
    if (!draftSet.has(id)) setDraft((current) => [...current, id])
    setManualValue("")
    setManualError(null)
  }

  const renderRow = (item: PickerItem) => {
    const elsewhere = ownedElsewhere(item)
    const checked = draftSet.has(item.id)
    // A conflicting row that is somehow already selected stays removable.
    const locked = (elsewhere || item.disabled) && !checked
    // The row is the click target, but only the item's name labels the box.
    const nameId = `${rowIdPrefix}-${encodeURIComponent(item.id)}`
    return (
      <Inline
        render={<label />}
        key={item.id}
        gap="md"
        align="start"
        className={cn(
          "cursor-pointer rounded-compact px-2 py-1.5 text-label text-ink transition-colors duration-fast ease-out-quint hover:bg-hover motion-reduce:transition-none",
          elsewhere && "opacity-60"
        )}
      >
        <Checkbox
          className="mt-0.5"
          aria-labelledby={nameId}
          checked={checked}
          disabled={locked}
          onCheckedChange={() => toggle(item.id)}
        />
        {item.icon && (
          <Box render={<span />} className="mt-0.5 shrink-0 text-ink-subtle">
            {item.icon}
          </Box>
        )}
        <Stack gap="none" className="min-w-0 flex-1">
          <Inline gap="sm" align="center" className="min-w-0">
            <Box
              render={<span id={nameId} />}
              className="truncate"
              title={item.label}
            >
              {item.label}
            </Box>
            {item.meta && (
              <Box
                render={<span />}
                className="shrink-0 text-meta text-ink-subtle"
              >
                {item.meta}
              </Box>
            )}
          </Inline>
          {item.warning && !elsewhere && (
            <Inline
              gap="xs"
              align="center"
              className="text-meta text-ink-subtle"
            >
              <Icon icon={AlertTriangle} size="sm" />
              <Box render={<span />}>{item.warning}</Box>
            </Inline>
          )}
        </Stack>
        {elsewhere && item.owner && (
          <Badge tier="quiet" tone="neutral">
            {item.owner.name}
          </Badge>
        )}
      </Inline>
    )
  }

  const renderGroup = (heading: string, group: Array<PickerItem>) =>
    group.length > 0 && (
      <Stack gap="none">
        <Box className="px-2 pt-2 pb-1 text-meta font-medium text-ink-subtle">
          {heading} · {group.length}
        </Box>
        {group.map(renderRow)}
      </Stack>
    )

  const empty = inThis.length + available.length + owned.length === 0

  // A centered dialog rather than an anchored popover: the panel is tall
  // enough that a popover flips up or down with the trigger's position, and
  // the two pickers in one form would open in opposite directions.
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setDraft(selected)
          setSearch("")
          setExtras([])
          setManualValue("")
          setManualError(null)
        }
        setOpen(next)
      }}
    >
      <DialogTrigger
        render={<Button size="compact" variant="outline" disabled={disabled} />}
      >
        {triggerLabel}
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader className="pr-8">
          <Inline gap="sm" align="center" justify="between" wrap>
            <DialogTitle>{title}</DialogTitle>
            {actions}
          </Inline>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <Stack gap="sm">
          <Inline gap="md" align="center">
            <SearchInput
              className="min-w-0 flex-1"
              label={searchPlaceholder}
              placeholder={searchPlaceholder}
              value={search}
              onValueChange={setSearch}
            />
            {filter && (
              <Inline
                render={<label />}
                gap="sm"
                align="center"
                className="shrink-0 text-meta text-ink-subtle"
              >
                {/* The enclosing label names the switch; an aria-label as well
                    would double the announced name. */}
                <Switch checked={filterOn} onCheckedChange={setFilterOn} />
                {filter.label}
              </Inline>
            )}
          </Inline>
          {filters && (
            <ToggleGroup
              aria-label={`${title} filter`}
              value={[activeFilter ?? ALL_FILTER]}
              onValueChange={(groupValue) => {
                const next = groupValue[0]
                setActiveFilter(!next || next === ALL_FILTER ? null : next)
              }}
            >
              <ToggleGroupItem value={ALL_FILTER}>All</ToggleGroupItem>
              {filters.map((option) => (
                <ToggleGroupItem key={option.label} value={option.label}>
                  {option.label}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
        </Stack>
        <Box border="line" radius="compact" className="overflow-hidden">
          <ScrollArea overflow="vertical" viewportClassName="max-h-80">
            <Stack gap="xs" padding="xs">
              {notice && (
                <Inline
                  gap="xs"
                  align="center"
                  className="px-2 pt-1 text-meta text-ink-subtle"
                >
                  <Icon icon={AlertTriangle} size="sm" />
                  <Box render={<span />}>{notice}</Box>
                </Inline>
              )}
              {loading && (
                <Stack gap="xs" padding="xs">
                  <Skeleton className="h-control-sm w-full" />
                  <Skeleton className="h-control-sm w-full" />
                </Stack>
              )}
              {loadError && (
                <StateNotice
                  tone="RISK"
                  icon={AlertTriangle}
                  title={`Could not list ${pluralNoun}`}
                  description={loadError}
                />
              )}
              {!loading && !loadError && empty && (
                <EmptyState icon={Search} title="Nothing matches." />
              )}
              {renderGroup("In this workspace", inThis)}
              {renderGroup("Available", available)}
              {renderGroup("Owned by another workspace", owned)}
            </Stack>
          </ScrollArea>
        </Box>
        {manual && (
          <Inline
            render={
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  addManual()
                }}
              />
            }
            gap="sm"
            align="end"
          >
            <Box className="min-w-0 flex-1">
              <FormField
                label={manual.label}
                help={manual.hint}
                error={manualError ?? undefined}
                control={
                  <Input
                    placeholder={manual.placeholder}
                    value={manualValue}
                    onChange={(event) => {
                      setManualValue(event.target.value)
                      setManualError(null)
                    }}
                  />
                }
              />
            </Box>
            <Button
              type="submit"
              variant="outline"
              disabled={!manualValue.trim()}
            >
              Add
            </Button>
          </Inline>
        )}
        <DialogFooter className="sm:items-center sm:justify-between">
          <Box render={<span />} className="text-meta text-ink-subtle">
            {hiddenCount > 0 && (
              <Tooltip>
                <TooltipTrigger className="cursor-help underline decoration-dotted underline-offset-4">
                  {hiddenCount} hidden by the filter
                </TooltipTrigger>
                <TooltipContent>
                  Hidden by{" "}
                  {[filterActive && filter.label, categoryFilter?.label]
                    .filter(Boolean)
                    .map((label) => `“${label}”`)
                    .join(" and ")}
                  . Change the filters above to show more {pluralNoun}.
                </TooltipContent>
              </Tooltip>
            )}
          </Box>
          <Inline gap="sm">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                onChange(draft)
                setOpen(false)
              }}
            >
              Save {draft.length} {draft.length === 1 ? noun : pluralNoun}
            </Button>
          </Inline>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
