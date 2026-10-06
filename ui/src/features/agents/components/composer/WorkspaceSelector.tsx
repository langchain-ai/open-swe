import { useState } from "react"

import {
  COMPOSER_POPUP_ROW_CLASS,
  ComposerControl,
  ComposerControlChevron,
} from "./ComposerControl"
import type { WorkspaceOption } from "@/lib/api"
import { Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import { Check, Stack as StackGlyph } from "@/components/glyphs"
import { cn } from "@/lib/utils"

interface WorkspaceSelectorProps {
  workspaces: Array<WorkspaceOption>
  selectedSlug: string | null
  onChange: (slug: string | null) => void
  disabled?: boolean
  /** Render even with a single workspace, where a choice must still be made. */
  showWithOneWorkspace?: boolean
  /** Shown while nothing is selected. */
  placeholder?: string
}

/**
 * Picks the workspace a new thread's sandbox boots from.
 *
 * Only rendered when there is more than one to choose between — with a single
 * workspace (or none) the choice is already made, so the control would be
 * noise.
 */
export function WorkspaceSelector({
  workspaces,
  selectedSlug,
  onChange,
  disabled = false,
  showWithOneWorkspace = false,
  placeholder = "No workspace",
}: WorkspaceSelectorProps) {
  const [open, setOpen] = useState(false)

  if (workspaces.length < (showWithOneWorkspace ? 1 : 2)) return null

  const selected = workspaces.find(
    (workspace) => workspace.slug === selectedSlug
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <ComposerControl
            aria-label="Workspace"
            className="max-w-56"
            disabled={disabled}
          >
            <Icon icon={StackGlyph} size="sm" />
            <span className="min-w-0 truncate">
              {selected?.name ?? placeholder}
            </span>
            <ComposerControlChevron />
          </ComposerControl>
        }
      />
      <PopoverContent
        align="start"
        className="max-h-72 w-64"
        inset="flush"
        scrollable
      >
        <Stack padding="xs">
          <p className="px-1.5 py-1 text-meta font-medium text-ink-subtle">
            Workspace
          </p>
          {workspaces.map((workspace) => {
            const isSelected = workspace.slug === selectedSlug
            return (
              <button
                key={workspace.slug}
                type="button"
                onClick={() => {
                  onChange(workspace.slug)
                  setOpen(false)
                }}
                className={cn(
                  COMPOSER_POPUP_ROW_CLASS,
                  "cursor-pointer hover:bg-hover focus-visible:bg-hover"
                )}
              >
                <span className="min-w-0 truncate">{workspace.name}</span>
                {!workspace.has_snapshot && (
                  <span className="shrink-0 text-meta text-ink-subtle">
                    no snapshot
                  </span>
                )}
                {isSelected && (
                  <Icon
                    icon={Check}
                    size="sm"
                    className="ml-auto text-ink-subtle"
                  />
                )}
              </button>
            )
          })}
        </Stack>
      </PopoverContent>
    </Popover>
  )
}
