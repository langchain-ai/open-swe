import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@langchain/gtm-platform-design-system/ui/dialog"
import { ScrollArea } from "@langchain/gtm-platform-design-system/ui/scroll-area"
import {
  Shortcut,
  type ShortcutKey,
} from "@langchain/gtm-platform-design-system/ui/shortcut"

import type { AppCommand } from "@/lib/appCommands"
import { shortcutPlatform } from "@/lib/hotkeys"
import { useIsHydrated } from "@/lib/hydration"

const KEY_ALIASES: Record<string, ShortcutKey> = {
  cmd: "meta",
  command: "meta",
  control: "ctrl",
  option: "alt",
  escape: "esc",
  return: "enter",
  space: "Space",
}

/** An app shortcut string ("mod+shift+p") as the chord the system draws. */
function shortcutKeys(shortcut: string): ReadonlyArray<ShortcutKey> {
  const parts = shortcut
    .toLowerCase()
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean)
  // "?" is typed with Shift, but nobody reads it as Shift+?.
  if (parts.length === 2 && parts[0] === "shift" && parts[1] === "?")
    return ["?"]
  return parts.map((part) => KEY_ALIASES[part] ?? part)
}

/** One shortcut as a row of keys, on the platform's modifier once hydrated. */
export function ShortcutChord({
  shortcut,
  className,
}: {
  shortcut: string
  className?: string
}) {
  const hydrated = useIsHydrated()
  return (
    <Shortcut
      keys={shortcutKeys(shortcut)}
      apple={hydrated && shortcutPlatform() === "mac"}
      className={className}
    />
  )
}

export function AppShortcutReference({
  commands,
  open,
  onOpenChange,
}: {
  commands: ReadonlyArray<AppCommand>
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const groups = new Map<string, Array<AppCommand>>()
  for (const command of commands) {
    if (!command.shortcuts?.length) continue
    const group = groups.get(command.group) ?? []
    group.push(command)
    groups.set(command.group, group)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-hotkeys="ignore"
        aria-label="Keyboard shortcuts"
        className="sm:max-w-xl"
      >
        <DialogHeader>
          <DialogTitle className="text-title">Keyboard shortcuts</DialogTitle>
          <DialogDescription className="sr-only">
            Keyboard shortcuts available in the current view.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea
          overflow="vertical"
          viewportClassName="max-h-96"
          className="-mx-4 min-h-0"
        >
          <Stack gap="xl" className="px-4 pb-1">
            {[...groups].map(([group, groupCommands]) => (
              <Stack
                key={group}
                render={<section aria-label={group} />}
                gap="xs"
              >
                <Box
                  render={<h3 />}
                  className="text-meta font-medium text-ink-subtle"
                >
                  {group}
                </Box>
                <Stack gap="none" className="divide-y divide-line">
                  {groupCommands.map((command) => (
                    <Inline
                      key={command.id}
                      gap="md"
                      align="center"
                      className="min-h-row-data py-1"
                    >
                      <Box
                        render={<span />}
                        className="min-w-0 flex-1 text-label text-ink"
                      >
                        {command.label}
                      </Box>
                      <Inline gap="sm" align="center" className="shrink-0">
                        {command.shortcuts?.map((shortcut) => (
                          <ShortcutChord
                            key={`${command.id}:${shortcut}`}
                            shortcut={shortcut}
                          />
                        ))}
                      </Inline>
                    </Inline>
                  ))}
                </Stack>
              </Stack>
            ))}
          </Stack>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
