import { memo, useLayoutEffect, useRef } from "react";
import { Bot, File as FileIcon } from "lucide-react";

import type { ComposerTriggerKind } from "./composerTrigger";
import { cn } from "@/lib/utils";

export type ComposerCommandItem =
  | { id: string; type: "path"; path: string; label: string; description: string }
  | { id: string; type: "slash-command"; command: string; label: string; description: string };

interface ComposerCommandMenuProps {
  items: Array<ComposerCommandItem>;
  triggerKind: ComposerTriggerKind;
  activeItemId: string | null;
  emptyStateText?: string;
  onHighlight: (itemId: string) => void;
  onSelect: (item: ComposerCommandItem) => void;
}

/**
 * The autocomplete popup for `@path` and `/command`. Keyboard navigation lives
 * in the composer (the editor keeps focus while this is open), so this only
 * reflects the active item and reports pointer intent back up.
 */
export const ComposerCommandMenu = memo(function ComposerCommandMenu({
  items,
  triggerKind,
  activeItemId,
  emptyStateText,
  onHighlight,
  onSelect,
}: ComposerCommandMenuProps) {
  const listRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!activeItemId || !listRef.current) return;
    listRef.current
      .querySelector<HTMLElement>(`[data-composer-item-id="${CSS.escape(activeItemId)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeItemId]);

  return (
    <div
      className="dropdown-glass absolute bottom-full left-0 z-50 mb-2 w-full max-w-md overflow-hidden rounded-xl"
      role="listbox"
      aria-label={triggerKind === "path" ? "Files" : "Commands"}
    >
      {items.length > 0 ? (
        <div ref={listRef} className="max-h-64 overflow-y-auto py-1">
          {items.map((item) => (
            <button
              aria-selected={activeItemId === item.id}
              className={cn(
                "flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs/relaxed select-none",
                activeItemId === item.id ? "bg-accent text-accent-foreground" : "text-foreground",
              )}
              data-composer-item-id={item.id}
              key={item.id}
              // The editor must not lose focus, or the caret position the
              // insertion is anchored to disappears before the click lands.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => {
                if (activeItemId !== item.id) onHighlight(item.id);
              }}
              onClick={() => onSelect(item)}
              role="option"
              type="button"
            >
              {item.type === "path" ? (
                <FileIcon className="size-3.5 shrink-0 text-muted-foreground/80" />
              ) : (
                <Bot className="size-3.5 shrink-0 text-muted-foreground/80" />
              )}
              <span className="shrink-0 font-medium">{item.label}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground/70">
                {item.description}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <p className="px-4 py-3 text-xs text-muted-foreground/70">
          {emptyStateText ??
            (triggerKind === "path" ? "No matching files." : "No matching command.")}
        </p>
      )}
    </div>
  );
});
