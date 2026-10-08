import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"

import type { AppCommand } from "@/lib/appCommands"
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
      <DialogContent
        className="max-h-[min(40rem,80vh)] w-[min(38rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-default bg-elevated text-primary shadow-lg"
        childrenClassName="gap-0 p-0"
        showClose={false}
      >
        <div className="flex min-h-0 flex-col" data-hotkeys="ignore">
          <div className="flex items-center justify-between border-b border-default px-space-5 py-space-4">
            <DialogTitle className="text-sm font-medium">
              Keyboard shortcuts
            </DialogTitle>
            <DialogClose asChild>
              <IconButton
                color="secondary"
                icon={XIcon}
                label="Close keyboard shortcuts"
                size="xs"
                tooltipProps={{ disabled: true }}
                variant="plain"
              />
            </DialogClose>
          </div>
          <DialogDescription className="sr-only">
            Keyboard shortcuts available in the current view.
          </DialogDescription>
          <div className="overflow-y-auto p-space-5">
            {[...groups].map(([group, groupCommands]) => (
              <section className="mb-space-5 last:mb-0" key={group}>
                <h3 className="mb-space-2 text-xxs font-semibold tracking-wide text-tertiary uppercase">
                  {group}
                </h3>
                <div className="divide-y divide-subtle rounded-lg border border-default">
                  {groupCommands.map((command) => (
                    <div
                      className="flex min-h-10 items-center gap-space-3 px-space-3 py-space-2"
                      key={command.id}
                    >
                      <span className="min-w-0 flex-1 text-sm">
                        {command.label}
                      </span>
                      <div className="flex shrink-0 items-center gap-space-1">
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
        </div>
      </DialogContent>
    </Dialog>
  )
}
