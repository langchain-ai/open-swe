import { useEffect, useRef, useState } from "react"
import { CaretDownIcon, StackIcon } from "@phosphor-icons/react"

import type { WorkspaceOption } from "@/lib/api"
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
  side?: "top" | "bottom"
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
  side = "bottom",
}: WorkspaceSelectorProps) {
  const [open, setOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (dropdownRef.current && !dropdownRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  if (workspaces.length < (showWithOneWorkspace ? 1 : 2)) return null

  const selected = workspaces.find(
    (workspace) => workspace.slug === selectedSlug
  )

  return (
    <div ref={dropdownRef} className="relative min-w-0 shrink">
      <button
        type="button"
        disabled={disabled}
        aria-label="Workspace"
        onClick={() => setOpen((value) => !value)}
        className="flex max-w-[220px] cursor-pointer items-center gap-1 text-secondary transition-opacity hover:opacity-80 disabled:cursor-default disabled:opacity-60"
      >
        <StackIcon className="size-3.5 shrink-0" />
        <span className="flex-1 truncate text-left">
          {selected?.name ?? placeholder}
        </span>
        <CaretDownIcon className="size-3 shrink-0 opacity-70" />
      </button>
      {open && (
        <div
          className={cn(
            "absolute left-0 z-50 flex max-h-72 w-64 flex-col overflow-y-auto rounded border border-default bg-elevated text-xs text-primary shadow-lg",
            side === "top" ? "bottom-full mb-1" : "top-full mt-1"
          )}
        >
          <div className="px-2 pt-2 pb-1 text-secondary">Workspace</div>
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
                  "flex w-full items-center px-2 py-1.5 text-left transition-colors hover:bg-surface-level-2",
                  isSelected ? "text-primary" : "text-secondary"
                )}
              >
                <span className="truncate">{workspace.name}</span>
                {!workspace.has_snapshot && (
                  <span className="ml-2 shrink-0 text-[10px] text-secondary">
                    no snapshot
                  </span>
                )}
                {isSelected && (
                  <span className="ml-auto pl-3 text-secondary">✓</span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
