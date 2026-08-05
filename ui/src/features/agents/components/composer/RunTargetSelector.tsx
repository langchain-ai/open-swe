import { Check, Cloud, FolderOpen, Laptop, LockKeyhole } from "lucide-react"

import { ComposerControlChevron } from "./ComposerControl"
import {
  Menu,
  MenuGroup,
  MenuGroupLabel,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "@/components/ui/menu"

export type RunTarget = "cloud" | "local"

interface RunTargetSelectorProps {
  value: RunTarget
  onChange: (value: RunTarget) => void
  localEnabled?: boolean
}

export function RunTargetSelector({
  value,
  onChange,
  localEnabled = false,
}: RunTargetSelectorProps) {
  const Icon = value === "local" ? Laptop : Cloud
  return (
    <Menu>
      <MenuTrigger className="flex items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80">
        <Icon className="size-3.5" />
        <span>{value === "local" ? "My Mac" : "Cloud"}</span>
        <ComposerControlChevron />
      </MenuTrigger>
      <MenuPopup align="start" className="w-56" sideOffset={7}>
        <MenuGroup>
          <MenuGroupLabel>Run on</MenuGroupLabel>
          <MenuItem onClick={() => onChange("cloud")}>
            <Cloud />
            Cloud
            {value === "cloud" && <Check className="ml-auto" />}
          </MenuItem>
          <MenuItem disabled={!localEnabled} onClick={() => onChange("local")}>
            <Laptop />
            My Mac
            {!localEnabled && <LockKeyhole className="ml-auto" />}
            {localEnabled && value === "local" && <Check className="ml-auto" />}
          </MenuItem>
        </MenuGroup>
      </MenuPopup>
    </Menu>
  )
}

interface LocalProjectSelectorProps {
  path: string | null
  onPick: () => void
}

export function LocalProjectSelector({
  path,
  onPick,
}: LocalProjectSelectorProps) {
  const label = path?.split(/[\\/]/).filter(Boolean).at(-1) || "Choose project"
  return (
    <button
      className="flex max-w-[260px] items-center gap-1 text-muted-foreground transition-opacity hover:opacity-80"
      onClick={onPick}
      title={path || "Choose a local project folder"}
      type="button"
    >
      <FolderOpen className="size-3.5 shrink-0" />
      <span className="truncate">{label}</span>
      <ComposerControlChevron />
    </button>
  )
}
