import { memo, useLayoutEffect, useRef } from "react"
import { FileIcon } from "@phosphor-icons/react/dist/ssr/File"
import { HashIcon } from "@phosphor-icons/react/dist/ssr/Hash"
import { RobotIcon } from "@phosphor-icons/react/dist/ssr/Robot"

import type { ComposerTriggerKind } from "./composerTrigger"
import { cn } from "@/lib/utils"

export type ComposerCommandItem =
  | {
      id: string
      type: "path"
      path: string
      label: string
      description: string
    }
  | {
      id: string
      type: "slash-command"
      command: string
      label: string
      description: string
    }
  | {
      id: string
      type: "skill"
      name: string
      label: string
      description: string
    }
  | {
      id: string
      type: "slack-channel"
      channelId: string
      name: string
      label: string
      description: string
    }

interface ComposerCommandMenuProps {
  items: Array<ComposerCommandItem>
  triggerKind: ComposerTriggerKind
  activeItemId: string | null
  emptyStateText?: string
  onHighlight: (itemId: string) => void
  onSelect: (item: ComposerCommandItem) => void
}

/**
 * The autocomplete popup for `@path`, `/command`, `$skill`, and `#channel`. Keyboard
 * navigation lives in the composer (the editor keeps focus while this is open),
 * so this only reflects the active item and reports pointer intent back up.
 */
export const ComposerCommandMenu = memo(function ComposerCommandMenu({
  items,
  triggerKind,
  activeItemId,
  emptyStateText,
  onHighlight,
  onSelect,
}: ComposerCommandMenuProps) {
  const listRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!activeItemId || !listRef.current) return
    listRef.current
      .querySelector<HTMLElement>(
        `[data-composer-item-id="${CSS.escape(activeItemId)}"]`
      )
      ?.scrollIntoView({ block: "nearest" })
  }, [activeItemId])

  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-2 w-full max-w-md overflow-hidden rounded-lg border border-subtle bg-elevated shadow-md"
      role="listbox"
      aria-label={
        triggerKind === "path"
          ? "Files"
          : triggerKind === "skill-command"
            ? "Skills"
            : triggerKind === "slack-channel"
              ? "Slack channels"
              : "Commands"
      }
    >
      {items.length > 0 ? (
        <div ref={listRef} className="max-h-64 overflow-y-auto py-1">
          {items.map((item) => (
            <button
              aria-selected={activeItemId === item.id}
              className={cn(
                "flex w-full cursor-pointer items-center gap-space-2 px-space-3 py-1.5 text-left text-xs/relaxed text-primary select-none",
                activeItemId === item.id && "bg-elevated-hover"
              )}
              data-composer-item-id={item.id}
              key={item.id}
              // The editor must not lose focus, or the caret position the
              // insertion is anchored to disappears before the click lands.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => {
                if (activeItemId !== item.id) onHighlight(item.id)
              }}
              onClick={() => onSelect(item)}
              role="option"
              type="button"
            >
              {item.type === "path" ? (
                <FileIcon
                  className="size-3.5 shrink-0 text-icon-tertiary"
                  weight="regular"
                />
              ) : item.type === "slack-channel" ? (
                <HashIcon
                  className="size-3.5 shrink-0 text-icon-tertiary"
                  weight="regular"
                />
              ) : (
                <RobotIcon
                  className="size-3.5 shrink-0 text-icon-tertiary"
                  weight="regular"
                />
              )}
              <span className="shrink-0 font-medium">{item.label}</span>
              <span className="min-w-0 flex-1 truncate text-tertiary">
                {item.description}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className="px-4 py-3 text-xs text-tertiary">
          {emptyStateText ??
            (triggerKind === "path"
              ? "No matching files."
              : "No matching command.")}
        </p>
      )}
    </div>
  )
})
