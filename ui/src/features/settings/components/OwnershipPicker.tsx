import { useMemo, useState, type ReactNode } from "react"
import { MagnifyingGlassIcon, WarningIcon } from "@phosphor-icons/react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverDescription,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
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
}

export interface PickerFilter {
  label: string
  matches: (item: PickerItem) => boolean
}

export interface ManualEntry {
  label: string
  placeholder: string
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
  manual?: ManualEntry
  loading?: boolean
  loadError?: string | null
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
  manual,
  loading = false,
  loadError = null,
  disabled = false,
}: OwnershipPickerProps) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Array<string>>(selected)
  const [search, setSearch] = useState("")
  const [filterOn, setFilterOn] = useState(true)
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
    !filterActive || filter.matches(item)

  const inThis = draft
    .map((id) => rows.get(id))
    .filter((item): item is PickerItem => !!item)
    .filter((item) => matchesSearch(item, search))
  const rest = [...rows.values()].filter(
    (item) => !draftSet.has(item.id) && matchesSearch(item, search)
  )
  const available = rest.filter(
    (item) => !ownedElsewhere(item) && passesFilter(item)
  )
  const owned = rest.filter(
    (item) => ownedElsewhere(item) && passesFilter(item)
  )
  const hiddenCount = filterActive
    ? rest.filter((item) => !filter.matches(item)).length
    : 0

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
    if (!rows.has(id)) setExtras((current) => [...current, { id, label: id }])
    if (!draftSet.has(id)) setDraft((current) => [...current, id])
    setManualValue("")
    setManualError(null)
  }

  const renderRow = (item: PickerItem) => {
    const elsewhere = ownedElsewhere(item)
    const checked = draftSet.has(item.id)
    return (
      <label
        key={item.id}
        className={cn(
          "flex items-start gap-3 px-3 py-2 text-sm transition-colors hover:bg-muted/40",
          elsewhere && "opacity-60"
        )}
      >
        <input
          type="checkbox"
          className="mt-1 size-3.5 shrink-0 accent-primary"
          aria-label={item.label}
          checked={checked}
          disabled={elsewhere}
          onChange={() => toggle(item.id)}
        />
        {item.icon && (
          <span className="mt-0.5 shrink-0 text-muted-foreground">
            {item.icon}
          </span>
        )}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-center gap-2">
            <span className="truncate">{item.label}</span>
            {item.meta && (
              <span className="shrink-0 text-xs text-muted-foreground">
                {item.meta}
              </span>
            )}
          </span>
          {item.warning && !elsewhere && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <WarningIcon size={12} /> {item.warning}
            </span>
          )}
        </span>
        {elsewhere && item.owner && (
          <Badge variant="secondary">{item.owner.name}</Badge>
        )}
      </label>
    )
  }

  const renderGroup = (heading: string, group: Array<PickerItem>) =>
    group.length > 0 && (
      <div>
        <div className="px-3 pt-3 pb-1 text-xs font-medium text-muted-foreground">
          {heading} · {group.length}
        </div>
        {group.map(renderRow)}
      </div>
    )

  const empty = inThis.length + available.length + owned.length === 0

  return (
    <Popover
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
      <PopoverTrigger
        render={<Button size="sm" variant="outline" disabled={disabled} />}
      >
        {triggerLabel}
      </PopoverTrigger>
      <PopoverPopup
        align="start"
        className="w-[520px] max-w-[calc(100vw-2rem)] p-0"
      >
        <PopoverTitle className="px-3 pt-3">{title}</PopoverTitle>
        {description && (
          <PopoverDescription className="px-3 pt-1">
            {description}
          </PopoverDescription>
        )}
        <div className="flex items-center gap-3 px-3 pt-3 pb-2">
          <div className="relative min-w-0 flex-1">
            <MagnifyingGlassIcon
              className="pointer-events-none absolute top-1.5 left-2 text-muted-foreground"
              size={14}
            />
            <Input
              aria-label={searchPlaceholder}
              placeholder={searchPlaceholder}
              className="pl-7"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          {filter && (
            <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              {/* The enclosing label names the switch; an aria-label as well
                  would double the announced name. */}
              <Switch checked={filterOn} onCheckedChange={setFilterOn} />
              {filter.label}
            </label>
          )}
        </div>
        <div className="max-h-80 overflow-y-auto border-t border-border pb-2">
          {loading && (
            <p className="px-3 py-3 text-xs text-muted-foreground">Loading…</p>
          )}
          {loadError && (
            <p role="alert" className="px-3 py-3 text-xs text-destructive">
              {loadError}
            </p>
          )}
          {!loading && !loadError && empty && (
            <p className="px-3 py-3 text-xs text-muted-foreground">
              Nothing matches.
            </p>
          )}
          {renderGroup("In this workspace", inThis)}
          {renderGroup("Available", available)}
          {renderGroup("Owned by another workspace", owned)}
        </div>
        {manual && (
          <form
            className="flex items-start gap-2 border-t border-border px-3 py-2"
            onSubmit={(event) => {
              event.preventDefault()
              addManual()
            }}
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <Input
                aria-label={manual.label}
                placeholder={manual.placeholder}
                value={manualValue}
                onChange={(event) => {
                  setManualValue(event.target.value)
                  setManualError(null)
                }}
              />
              {manualError && (
                <span role="alert" className="text-xs text-destructive">
                  {manualError}
                </span>
              )}
            </div>
            <Button
              type="submit"
              size="sm"
              variant="outline"
              disabled={!manualValue.trim()}
            >
              Add
            </Button>
          </form>
        )}
        <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2">
          <span className="text-xs text-muted-foreground">
            {hiddenCount > 0 ? `${hiddenCount} hidden by the filter` : ""}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                onChange(draft)
                setOpen(false)
              }}
            >
              Save {draft.length} {draft.length === 1 ? noun : pluralNoun}
            </Button>
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  )
}
