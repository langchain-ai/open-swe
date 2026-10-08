import { CaretDownIcon, CloudIcon, LaptopIcon } from "@phosphor-icons/react"

import {
  Menu,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
} from "@/components/ui/menu"

export type ThreadTarget = "cloud" | "local"

const OPTIONS: Record<ThreadTarget, { label: string; Icon: typeof CloudIcon }> =
  {
    cloud: { label: "Cloud", Icon: CloudIcon },
    local: { label: "This Mac", Icon: LaptopIcon },
  }
const ORDER: ThreadTarget[] = ["cloud", "local"]

/** Where the thread runs; a change takes effect with the next message. */
export function ThreadTargetMenu({
  value,
  pending,
  onChange,
  disabled = false,
}: {
  value: ThreadTarget
  pending: boolean
  onChange: (next: ThreadTarget) => void
  disabled?: boolean
}) {
  const current = OPTIONS[value]
  return (
    <Menu>
      <MenuTrigger
        aria-label="Where the thread runs"
        title={pending ? "Moves with your next message" : undefined}
        disabled={disabled}
        data-no-drag=""
        className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-subtle px-2 text-xs text-primary transition-colors hover:bg-surface-level-2 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none disabled:opacity-60"
      >
        <current.Icon className="size-3.5" />
        {current.label}
        {pending && (
          <span className="text-secondary">· next message</span>
        )}
        <CaretDownIcon className="size-3 text-secondary" />
      </MenuTrigger>
      <MenuPopup align="end" className="min-w-36">
        <MenuRadioGroup
          value={value}
          onValueChange={(next) => {
            if (next !== value) onChange(next as ThreadTarget)
          }}
        >
          {ORDER.map((option) => {
            const { label, Icon } = OPTIONS[option]
            return (
              <MenuRadioItem key={option} value={option} closeOnClick>
                <span className="inline-flex items-center gap-2">
                  <Icon className="size-3.5 text-secondary" />
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
