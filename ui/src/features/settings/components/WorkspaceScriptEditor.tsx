import { useId, useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"

import { TextEditor } from "@/components/TextEditor"

function WorkspaceReposPopover({ repos }: { repos: string[] }) {
  const titleId = useId()
  return (
    <Popover>
      <PopoverTrigger className="cursor-pointer rounded-sm font-mono underline decoration-dotted underline-offset-4 hover:text-primary focus-visible:outline-2 focus-visible:outline-[color:var(--border-focus)]">
        OPENSWE_WORKSPACE_REPOS
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-labelledby={titleId}
        className="w-96 max-w-[calc(100vw-2rem)]"
      >
        <p id={titleId} className="text-sm font-medium text-primary">
          Expanded value
        </p>
        <pre className="mt-space-2 max-h-60 overflow-auto rounded-md bg-surface-level-2 p-space-3 font-mono text-xs break-all whitespace-pre-wrap text-primary">
          <code>{`OPENSWE_WORKSPACE_REPOS="${repos.join(" ")}"`}</code>
        </pre>
      </PopoverContent>
    </Popover>
  )
}

export function WorkspaceScriptEditor({
  label,
  repos,
  value,
  onChange,
  description = "Edit the shell script, then choose Save scripts on the settings page to apply your changes.",
}: {
  label: string
  repos: string[]
  value: string
  onChange: (value: string) => void
  description?: string
}) {
  const [open, setOpen] = useState(false)
  const lines = value ? value.trimEnd().split("\n").length : 0

  return (
    <>
      <div className="mt-space-2 flex items-center gap-space-2">
        <Button
          size="xs"
          color="secondary"
          variant="outlined"
          onClick={() => setOpen(true)}
        >
          {`Edit ${label.toLowerCase()}`}
        </Button>
        <span className="text-xs text-secondary">
          {lines
            ? `${lines} ${lines === 1 ? "line" : "lines"}`
            : "Not configured"}
        </span>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          title={label}
          description={description}
          className="w-[56rem] max-w-[calc(100vw-2rem)]"
          childrenClassName="gap-0 p-0"
        >
          <p className="border-b border-default px-space-4 pb-space-4 text-sm text-secondary">
            Bound repositories are available in{" "}
            <WorkspaceReposPopover repos={repos} /> to preload; runs clone any
            other repository on demand.
          </p>
          <div className="h-[min(60vh,600px)]">
            <TextEditor
              language="shell"
              ariaLabel={label}
              value={value}
              onChange={onChange}
            />
          </div>
          <div className="flex justify-end border-t border-default p-space-3">
            <Button size="xs" color="primary" onClick={() => setOpen(false)}>
              Done
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
