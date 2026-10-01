import type { WorkspaceOption } from "@/lib/api"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/**
 * Picks which workspace's settings a page reads and writes.
 *
 * Hidden with a single workspace — there is nothing to choose between, so the
 * control would just be noise.
 */
export function WorkspaceSelect({
  workspaces,
  value,
  onChange,
}: {
  workspaces: Array<WorkspaceOption>
  value: string
  onChange: (slug: string) => void
}) {
  if (workspaces.length < 2) return null
  const items = workspaces.map((workspace) => ({
    value: workspace.slug,
    label: workspace.name,
  }))
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-xs font-medium text-muted-foreground">
        Workspace
      </span>
      <Select
        items={items}
        value={value}
        onValueChange={(next) => {
          if (next) onChange(next)
        }}
      >
        <SelectTrigger className="w-56">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((workspace) => (
            <SelectItem key={workspace.value} value={workspace.value}>
              {workspace.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
