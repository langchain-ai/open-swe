import { RowsIcon, SquareSplitHorizontalIcon } from "@phosphor-icons/react"

import type { DiffStyle } from "@/components/diff/diffUtils"
import { cn } from "@/lib/utils"

export function DiffStyleToggle({
  value,
  onChange,
}: {
  value: DiffStyle
  onChange: (value: DiffStyle) => void
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-md border border-border p-0.5">
      <DiffStyleButton
        active={value === "unified"}
        label="Unified view"
        onClick={() => onChange("unified")}
      >
        <RowsIcon className="size-3.5" />
      </DiffStyleButton>
      <DiffStyleButton
        active={value === "split"}
        label="Split view"
        onClick={() => onChange("split")}
      >
        <SquareSplitHorizontalIcon className="size-3.5" />
      </DiffStyleButton>
    </div>
  )
}

function DiffStyleButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        "flex size-5 items-center justify-center rounded text-muted-foreground transition-colors",
        active ? "bg-muted text-foreground" : "hover:text-foreground"
      )}
    >
      {children}
    </button>
  )
}
