import { ChevronDownIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Menu,
  MenuCheckboxItem,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu"

export function MultiSelect<T extends string>({
  label,
  placeholder,
  options,
  value,
  onValueChange,
}: {
  label: string
  placeholder: string
  options: readonly T[]
  value: readonly T[]
  onValueChange: (value: T[]) => void
}) {
  return (
    <Menu>
      <MenuTrigger render={<Button variant="outline" />} aria-label={label}>
        {value.length === 0
          ? placeholder
          : value.length === 1
            ? value[0]
            : `${value.length} selected`}
        <ChevronDownIcon aria-hidden="true" />
      </MenuTrigger>
      <MenuPopup align="start" className="min-w-48">
        {options.map((option) => (
          <MenuCheckboxItem
            key={option}
            checked={value.includes(option)}
            closeOnClick={false}
            onCheckedChange={(checked) =>
              onValueChange(
                checked
                  ? [...value, option]
                  : value.filter((item) => item !== option)
              )
            }
          >
            {option}
          </MenuCheckboxItem>
        ))}
        <MenuSeparator />
        <MenuItem
          disabled={value.length === 0}
          closeOnClick={false}
          onClick={() => onValueChange([])}
        >
          Clear selection
        </MenuItem>
      </MenuPopup>
    </Menu>
  )
}
