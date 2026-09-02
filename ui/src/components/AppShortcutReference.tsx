import { Dialog } from "@base-ui/react/dialog"
import { X } from "lucide-react"

import type { AppCommand } from "@/lib/appCommands"
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
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/45 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup
          className="fixed top-1/2 left-1/2 z-50 flex max-h-[min(40rem,80vh)] w-[min(38rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95"
          data-hotkeys="ignore"
        >
          <div className="flex items-center justify-between border-b border-border px-5 py-4">
            <Dialog.Title className="font-heading text-sm font-medium">
              Keyboard shortcuts
            </Dialog.Title>
            <Dialog.Close
              aria-label="Close keyboard shortcuts"
              className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <X className="size-4" />
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">
            Keyboard shortcuts available in the current view.
          </Dialog.Description>
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
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
