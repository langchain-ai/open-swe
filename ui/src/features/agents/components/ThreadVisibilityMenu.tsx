import { CaretDownIcon, GlobeIcon, LockIcon } from "@phosphor-icons/react"

import {
  Menu,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
} from "@/components/ui/menu"
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
    <Menu>
      <MenuTrigger
        aria-label="Thread visibility"
        aria-busy={busy}
        disabled={busy}
        data-no-drag=""
        className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border/60 px-2 text-xs text-foreground transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-60"
      >
        <current.Icon className="size-3.5" />
        {current.label}
        <CaretDownIcon className="size-3 text-muted-foreground" />
      </MenuTrigger>
      <MenuPopup align="end" className="min-w-36">
        <MenuRadioGroup
          value={value}
          onValueChange={(next) => {
            if (next !== value) onChange(next as ThreadVisibility)
          }}
        >
          {ORDER.map((option) => {
            const { label, Icon } = OPTIONS[option]
            return (
              <MenuRadioItem
                key={option}
                value={option}
                disabled={disabledValues.includes(option)}
                closeOnClick
              >
                <span className="inline-flex items-center gap-2">
                  <Icon className="size-3.5 text-muted-foreground" />
                  {label}
                </span>
              </MenuRadioItem>
            )
          })}
        </MenuRadioGroup>
      </MenuPopup>
    </Menu>
  )
}
