import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"
import { CloudIcon } from "@phosphor-icons/react/dist/ssr/Cloud"
import { LaptopIcon } from "@phosphor-icons/react/dist/ssr/Laptop"

import { MenuCheckItem } from "@/features/agents/components/MenuCheckItem"

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
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          color="secondary"
          variant="outlined"
          aria-label="Where the thread runs"
          title={pending ? "Moves with your next message" : undefined}
          disabled={disabled}
          data-no-drag=""
          leftDecorator={current.Icon}
          rightDecorator={CaretDownIcon}
          className="shrink-0"
        >
          {current.label}
          {pending && <span className="text-secondary">· next message</span>}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        {ORDER.map((option) => {
          const { label, Icon } = OPTIONS[option]
          return (
            <MenuCheckItem
              key={option}
              type="radio"
              checked={option === value}
              onSelect={() => {
                if (option !== value) onChange(option)
              }}
            >
              <Icon
                size={14}
                weight="regular"
                className="text-icon-secondary"
              />
              {label}
            </MenuCheckItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
