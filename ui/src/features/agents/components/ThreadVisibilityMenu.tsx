import { CaretDownIcon, GlobeIcon, LockIcon } from "@phosphor-icons/react"

import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import type { ThreadVisibility } from "@/lib/api"

const OPTIONS: Record<
  ThreadVisibility,
  { label: string; Icon: typeof LockIcon }
> = {
  private: { label: "Private", Icon: LockIcon },
  public: { label: "Workspace", Icon: GlobeIcon },
}
const ORDER: ThreadVisibility[] = ["private", "public"]

export function ThreadVisibilityMenu({
  value,
  onChange,
  disabledValues = [],
  busy = false,
}: {
  value: ThreadVisibility
  onChange: (next: ThreadVisibility) => void
  disabledValues?: ThreadVisibility[]
  busy?: boolean
}) {
  const current = OPTIONS[value]
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Thread visibility"
        aria-busy={busy}
        disabled={busy}
        data-no-drag=""
        className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-badge border border-line/60 px-2 text-label text-ink transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none disabled:opacity-60"
      >
        <current.Icon className="size-3.5" />
        {current.label}
        <CaretDownIcon className="size-3 text-ink-subtle" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => {
            if (next !== value) onChange(next as ThreadVisibility)
          }}
        >
          {ORDER.map((option) => {
            const { label, Icon } = OPTIONS[option]
            return (
              <DropdownMenuRadioItem
                key={option}
                value={option}
                disabled={disabledValues.includes(option)}
                closeOnClick
              >
                <span className="inline-flex items-center gap-2">
                  <Icon className="size-3.5 text-ink-subtle" />
                  {label}
                </span>
              </DropdownMenuRadioItem>
            )
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
