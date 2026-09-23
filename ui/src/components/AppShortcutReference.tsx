import { X } from "lucide-react"

import type { AppCommand } from "@/lib/appCommands"
import { IconButton } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogPopup,
  DialogTitle,
} from "@/components/ui/dialog"
import { Kbd } from "@/components/ui/kbd"
import { useShortcutLabel } from "@/lib/hotkeys"

function ShortcutKey({ shortcut }: { shortcut: string }) {
  const label = useShortcutLabel(shortcut)
  return <Kbd>{label}</Kbd>
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
      <DialogPopup
        className="max-h-[min(40rem,80vh)] max-w-[38rem]"
        data-hotkeys="ignore"
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <DialogTitle className="font-heading font-medium">
            Keyboard shortcuts
          </DialogTitle>
          <DialogClose
            aria-label="Close keyboard shortcuts"
            render={
              <IconButton
                className="text-muted-foreground"
                size="icon-sm"
                variant="ghost"
              />
            }
          >
            <X className="size-4" />
          </DialogClose>
        </div>
        <DialogDescription className="sr-only">
          Keyboard shortcuts available in the current view.
        </DialogDescription>
        <div className="overflow-y-auto p-5">
          {[...groups].map(([group, groupCommands]) => (
            <section className="mb-6 last:mb-0" key={group}>
              <h3 className="mb-2 text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
                {group}
              </h3>
              <div className="divide-y divide-border/60 rounded-lg border border-border">
                {groupCommands.map((command) => (
                  <div
                    className="flex min-h-10 items-center gap-3 px-3 py-2"
                    key={command.id}
                  >
                    <span className="min-w-0 flex-1 text-sm">
                      {command.label}
                    </span>
                    <div className="flex shrink-0 items-center gap-1">
                      {command.shortcuts?.map((shortcut) => (
                        <ShortcutKey
                          key={`${command.id}:${shortcut}`}
                          shortcut={shortcut}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      </DialogPopup>
    </Dialog>
  )
}
