import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import type { Glyph } from "@/components/glyphs"
import type { ThreadVisibility } from "@/lib/api"
import { ChevronDown, Globe, Lock } from "@/components/glyphs"

const OPTIONS: Record<ThreadVisibility, { label: string; icon: Glyph }> = {
  private: { label: "Private", icon: Lock },
  public: { label: "Workspace", icon: Globe },
}
const ORDER: ReadonlyArray<ThreadVisibility> = ["private", "public"]

function isThreadVisibility(value: unknown): value is ThreadVisibility {
  return value === "private" || value === "public"
}

export function ThreadVisibilityMenu({
  value,
  onChange,
  disabledValues = [],
  busy = false,
}: {
  value: ThreadVisibility
  onChange: (next: ThreadVisibility) => void
  disabledValues?: ReadonlyArray<ThreadVisibility>
  busy?: boolean
}) {
  const current = OPTIONS[value]
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="outline"
            aria-label="Thread visibility"
            aria-busy={busy}
            disabled={busy}
            data-no-drag=""
          />
        }
      >
        <Icon icon={current.icon} size="sm" />
        {current.label}
        <Icon icon={ChevronDown} size="sm" className="text-ink-subtle" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => {
            if (isThreadVisibility(next) && next !== value) onChange(next)
          }}
        >
          {ORDER.map((option) => (
            <DropdownMenuRadioItem
              key={option}
              value={option}
              disabled={disabledValues.includes(option)}
              closeOnClick
            >
              <Icon
                icon={OPTIONS[option].icon}
                size="sm"
                className="text-ink-subtle"
              />
              {OPTIONS[option].label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
