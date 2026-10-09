import type { ComponentProps, ReactNode } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { ButtonGroup } from "@langchain/macaw-components/ButtonGroup"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { CaretDownIcon } from "@phosphor-icons/react/dist/ssr/CaretDown"

type ButtonProps = ComponentProps<typeof Button>

/** One action as a button, its alternatives behind the caret beside it. */
export function SplitButton({
  children,
  onClick,
  disabled,
  color = "secondary",
  variant = "outlined",
  size = "xs",
  className,
  "aria-label": ariaLabel,
  menuLabel,
  menu,
  menuDisabled = disabled,
  menuOpen,
  onMenuOpenChange,
  menuAlign = "end",
  menuSide = "bottom",
  icon,
}: {
  children: ReactNode
  icon?: ButtonProps["leftDecorator"]
  onClick: () => void
  disabled?: boolean
  color?: "primary" | "secondary"
  variant?: "normal" | "outlined" | "plain"
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
  menuAlign?: "start" | "end"
  menuSide?: "top" | "bottom"
}) {
  const button = (
    <Button
      leftDecorator={icon}
      color={color}
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
    <ButtonGroup
      color={color}
      variant={variant}
      size={size}
      className={className}
    >
      {button}
      <DropdownMenu open={menuOpen} onOpenChange={onMenuOpenChange}>
        <DropdownMenuTrigger asChild>
          <IconButton
            icon={CaretDownIcon}
            color={color}
            variant={variant}
            size={size}
            label={menuLabel}
            disabled={menuDisabled}
            tooltipProps={{ disabled: true }}
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent align={menuAlign} side={menuSide} sideOffset={6}>
          {menu}
        </DropdownMenuContent>
      </DropdownMenu>
    </ButtonGroup>
  )
}
