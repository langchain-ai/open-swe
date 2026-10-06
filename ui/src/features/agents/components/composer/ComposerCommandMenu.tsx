import { memo, useLayoutEffect, useRef } from "react"

import { COMPOSER_POPUP_ROW_CLASS } from "./ComposerControl"
import type { ComposerTriggerKind } from "./composerTrigger"
import type { Glyph } from "@/components/glyphs"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { POPUP_SURFACE_SHELL } from "@langchain/gtm-platform-design-system/ui/popup-surface"
import { Bot, File, Hash } from "@/components/glyphs"
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

const ITEM_GLYPH: Record<ComposerCommandItem["type"], Glyph> = {
  path: File,
  "slash-command": Bot,
  skill: Bot,
  "slack-channel": Hash,
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
      className={cn(
        "absolute inset-x-0 bottom-full z-50 mb-1.5 max-w-md overflow-hidden",
        POPUP_SURFACE_SHELL
      )}
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
        <div ref={listRef} className="max-h-64 overflow-y-auto p-1">
          {items.map((item) => (
            <button
              aria-selected={activeItemId === item.id}
              className={cn(
                COMPOSER_POPUP_ROW_CLASS,
                "cursor-pointer gap-2",
                activeItemId === item.id && "bg-hover"
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
              <Box
                render={<span />}
                className="flex size-control-sm shrink-0 items-center justify-center rounded-compact bg-hover text-ink-subtle"
              >
                <Icon icon={ITEM_GLYPH[item.type]} size="sm" />
              </Box>
              <span className="shrink-0 font-medium">{item.label}</span>
              <span className="min-w-0 flex-1 truncate text-meta text-ink-subtle">
                {item.description}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className="px-3 py-2.5 text-label text-ink-subtle">
          {emptyStateText ??
            (triggerKind === "path"
              ? "No matching files."
              : "No matching command.")}
        </p>
      )}
    </div>
  )
})
