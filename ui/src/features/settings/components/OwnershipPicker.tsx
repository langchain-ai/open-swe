import {
  MagnifyingGlassRegularIcon,
  WarningRegularIcon,
} from "@langchain/macaw-components/icons"
import { useMemo, useState, type ReactNode } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { Switch } from "@langchain/macaw-components/Switch"
import { Tooltip } from "@langchain/macaw-components/Tooltip"

import { cn } from "@/lib/utils"

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
    return (
      <label
        key={item.id}
        className={cn(
          "flex items-start gap-space-3 px-space-3 py-space-2 text-sm text-primary transition-colors hover:bg-surface-level-1-hover",
          elsewhere && "opacity-60"
        )}
      >
        <Checkbox
          size="sm"
          containerClassName="mt-0.5 shrink-0"
          aria-label={item.label}
          checked={checked}
          disabled={locked}
          onCheckedChange={() => toggle(item.id)}
        />
        {item.icon && (
          <span className="mt-0.5 shrink-0 text-icon-secondary">
            {item.icon}
          </span>
        )}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-center gap-space-2">
            <span className="truncate" title={item.label}>
              {item.label}
            </span>
            {item.meta && (
              <span className="shrink-0 text-xs text-secondary">
                {item.meta}
              </span>
            )}
          </span>
          {item.warning && !elsewhere && (
            <span className="flex items-center gap-space-1 text-xs text-secondary">
              <WarningRegularIcon size={12} className="text-icon-warning" />{" "}
              {item.warning}
            </span>
          )}
        </span>
        {elsewhere && item.owner && (
          <Badge color="secondary" size="xs">
            {item.owner.name}
          </Badge>
        )}
      </label>
    )
  }

  const renderGroup = (heading: string, group: Array<PickerItem>) =>
    group.length > 0 && (
      <div>
        <div className="px-space-3 pt-space-3 pb-space-1 text-xs font-medium text-secondary">
          {heading} · {group.length}
        </div>
        {group.map(renderRow)}
      </div>
    )

  const empty = inThis.length + available.length + owned.length === 0

  const openPicker = () => {
    setDraft(selected)
    setSearch("")
    setExtras([])
    setManualValue("")
    setManualError(null)
    setOpen(true)
  }

  // A centered dialog rather than an anchored popover: the panel is tall
  // enough that a popover flips up or down with the trigger's position, and
  // the two pickers in one form would open in opposite directions.
  return (
    <>
      <Button
        size="xs"
        color="secondary"
        variant="outlined"
        disabled={disabled}
        onClick={openPicker}
      >
        {triggerLabel}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => (next ? openPicker() : setOpen(false))}
      >
        <DialogContent
          showClose={false}
          className="w-[520px] max-w-[calc(100vw-2rem)]"
          childrenClassName="gap-0 p-0"
        >
          <div className="flex flex-wrap items-center justify-between gap-space-2 px-space-3 pt-space-3">
            <DialogTitle className="text-base font-semibold text-primary">
              {title}
            </DialogTitle>
            {actions}
          </div>
          {description && (
            <DialogDescription className="px-space-3 pt-space-1 text-sm text-secondary">
              {description}
            </DialogDescription>
          )}
          <div className="flex items-center gap-space-3 px-space-3 pt-space-3 pb-space-2">
            <Input
              aria-label={searchPlaceholder}
              placeholder={searchPlaceholder}
              size="md"
              className="min-w-0 flex-1"
              leftIcon={MagnifyingGlassRegularIcon}
              value={search}
              onChange={setSearch}
            />
            {filter && (
              <Switch
                label={filter.label}
                labelClassName="text-xs text-secondary"
                className="shrink-0"
                checked={filterOn}
                onChange={setFilterOn}
              />
            )}
          </div>
          {filters && (
            <div
              role="group"
              aria-label={`${title} filter`}
              className="flex gap-space-1 px-space-3 pb-space-2"
            >
              {[null, ...filters].map((option) => {
                const active = activeFilter === (option?.label ?? null)
                return (
                  <Button
                    key={option?.label ?? "All"}
                    size="xs"
                    color="secondary"
                    variant={active ? "outlined" : "plain"}
                    aria-pressed={active}
                    onClick={() => setActiveFilter(option?.label ?? null)}
                  >
                    {option?.label ?? "All"}
                  </Button>
                )
              })}
            </div>
          )}
          <div className="max-h-[50vh] min-h-0 flex-1 overflow-y-auto border-t border-default pb-space-2">
            {notice && (
              <p className="flex items-center gap-space-1 px-space-3 pt-space-3 text-xs text-secondary">
                <WarningRegularIcon size={12} className="text-icon-warning" />{" "}
                {notice}
              </p>
            )}
            {loading && (
              <p className="p-space-3 text-xs text-secondary">Loading…</p>
            )}
            {loadError && (
              <p
                role="alert"
                className="p-space-3 text-xs text-error-secondary"
              >
                {loadError}
              </p>
            )}
            {!loading && !loadError && empty && (
              <p className="p-space-3 text-xs text-secondary">
                Nothing matches.
              </p>
            )}
            {renderGroup("In this workspace", inThis)}
            {renderGroup("Available", available)}
            {renderGroup("Owned by another workspace", owned)}
          </div>
          {manual && (
            <form
              className="flex items-start gap-space-2 border-t border-default px-space-3 py-space-2"
              onSubmit={(event) => {
                event.preventDefault()
                addManual()
              }}
            >
              <Input
                aria-label={manual.label}
                placeholder={manual.placeholder}
                size="sm"
                className="min-w-0 flex-1"
                isError={!!manualError}
                hintText={manualError ? undefined : manual.hint}
                value={manualValue}
                onChange={(value) => {
                  setManualValue(value)
                  setManualError(null)
                }}
              />
              <Button
                type="submit"
                size="sm"
                color="secondary"
                variant="outlined"
                disabled={!manualValue.trim()}
              >
                Add
              </Button>
            </form>
          )}
          {manual && manualError && (
            <span
              role="alert"
              className="px-space-3 pb-space-2 text-xs text-error-secondary"
            >
              {manualError}
            </span>
          )}
          <div className="flex items-center justify-between gap-space-3 border-t border-default px-space-3 py-space-2">
            <span className="text-xs text-secondary">
              {hiddenCount > 0 && (
                <Tooltip
                  tooltipClassName="max-w-64"
                  title={`Hidden by ${[
                    filterActive && filter.label,
                    categoryFilter?.label,
                  ]
                    .filter(Boolean)
                    .map((label) => `“${label}”`)
                    .join(
                      " and "
                    )}. Change the filters above to show more ${pluralNoun}.`}
                >
                  <button
                    type="button"
                    className="cursor-help underline decoration-dotted underline-offset-4"
                  >
                    {hiddenCount} hidden by the filter
                  </button>
                </Tooltip>
              )}
            </span>
            <div className="flex gap-space-2">
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button
                size="xs"
                color="primary"
                onClick={() => {
                  onChange(draft)
                  setOpen(false)
                }}
              >
                {`Save ${draft.length} ${draft.length === 1 ? noun : pluralNoun}`}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
