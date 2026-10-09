import {
  CaretDownIcon,
  GlobeRegularIcon,
} from "@langchain/macaw-components/icons"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { LockIcon } from "@phosphor-icons/react/dist/ssr/Lock"

import { MenuCheckItem } from "@/features/agents/components/MenuCheckItem"
import type { ThreadVisibility } from "@/lib/api"

const OPTIONS: Record<
  ThreadVisibility,
  { label: string; Icon: IconComponent }
> = {
  private: { label: "Private", Icon: LockIcon },
  public: { label: "Workspace", Icon: GlobeRegularIcon },
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
      <DropdownMenuTrigger asChild>
        <Button
          color="secondary"
          variant="outlined"
          aria-label="Thread visibility"
          aria-busy={busy}
          disabled={busy}
          data-no-drag=""
          leftDecorator={current.Icon}
          rightDecorator={CaretDownIcon}
          className="shrink-0"
        >
          {current.label}
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
              disabled={disabledValues.includes(option)}
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
