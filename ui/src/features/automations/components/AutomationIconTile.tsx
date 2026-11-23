import type { IconComponent } from "@langchain/macaw-components/Icon"

/** A rounded tile holding an automation's or template's icon. */
export function AutomationIconTile({ icon: Icon }: { icon: IconComponent }) {
  return (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-level-2 text-icon-secondary transition-colors group-hover:bg-surface-level-3">
      <Icon size={18} weight="regular" />
    </span>
  )
}
