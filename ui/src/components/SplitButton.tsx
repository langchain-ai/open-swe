import type { ComponentProps, ReactNode } from "react"
import { CaretDownIcon } from "@phosphor-icons/react"

import { Button } from "@/components/ui/button"
import { ButtonGroup, ButtonGroupSeparator } from "@/components/ui/button-group"
import { Menu, MenuPopup, MenuTrigger } from "@/components/ui/menu"

type ButtonProps = ComponentProps<typeof Button>

/** One action as a button, its alternatives behind the caret beside it. */
export function SplitButton({
  children,
  onClick,
  disabled,
  variant = "outline",
  size = "sm",
  className,
  "aria-label": ariaLabel,
  menuLabel,
  menu,
  menuDisabled = disabled,
  menuOpen,
  onMenuOpenChange,
  menuPopup,
}: {
  children: ReactNode
  onClick: () => void
  disabled?: boolean
  variant?: ButtonProps["variant"]
  size?: ButtonProps["size"]
  className?: string
  "aria-label"?: string
  /** The caret's accessible name. */
  menuLabel: string
  /** The menu's items; without any, it's a plain button. */
  menu?: ReactNode
  menuDisabled?: boolean
  menuOpen?: boolean
  onMenuOpenChange?: (open: boolean) => void
  menuPopup?: Omit<ComponentProps<typeof MenuPopup>, "children">
}) {
  const button = (
    <Button
      variant={variant}
      size={size}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-live="polite"
      onClick={onClick}
      className={menu ? undefined : className}
    >
      {children}
    </Button>
  )
  if (!menu) return button
  return (
    <ButtonGroup className={className}>
      {button}
      {/* Borderless buttons need a rule to read as two. */}
      {variant !== "outline" && <ButtonGroupSeparator />}
      <Menu open={menuOpen} onOpenChange={onMenuOpenChange}>
        <MenuTrigger
          aria-label={menuLabel}
          disabled={menuDisabled}
          render={<Button variant={variant} size={size} className="px-1.5" />}
        >
          <CaretDownIcon />
        </MenuTrigger>
        <MenuPopup align="end" {...menuPopup}>
          {menu}
        </MenuPopup>
      </Menu>
    </ButtonGroup>
  )
}
