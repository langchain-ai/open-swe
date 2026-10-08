import Editor from "@monaco-editor/react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useResolvedTheme } from "@/lib/theme"

function WorkspaceReposPopover({ repos }: { repos: string[] }) {
  return (
    <Popover>
      <PopoverTrigger className="cursor-pointer rounded-sm font-mono underline decoration-dotted underline-offset-4 hover:text-primary focus-visible:outline-2 focus-visible:outline-[color:var(--border-focus)]">
        OPENSWE_WORKSPACE_REPOS
      </PopoverTrigger>
      <PopoverPopup align="start" className="w-96 max-w-[calc(100vw-2rem)]">
        <PopoverTitle>Expanded value</PopoverTitle>
        <pre className="mt-2 max-h-60 overflow-auto rounded-md bg-surface-level-2 p-3 font-mono text-xs break-all whitespace-pre-wrap">
          <code>{`OPENSWE_WORKSPACE_REPOS="${repos.join(" ")}"`}</code>
        </pre>
      </PopoverPopup>
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
  const theme = useResolvedTheme()
  const lines = value ? value.trimEnd().split("\n").length : 0

  return (
    <Dialog>
      <div className="mt-2 flex items-center gap-2">
        <DialogTrigger render={<Button size="sm" variant="outline" />}>
          Edit {label.toLowerCase()}
        </DialogTrigger>
        <span className="text-xs text-secondary">
          {lines
            ? `${lines} ${lines === 1 ? "line" : "lines"}`
            : "Not configured"}
        </span>
      </div>
      <DialogPopup className="max-w-4xl">
        <div className="space-y-2 border-b border-default p-4">
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
          <p className="text-sm text-secondary">
            Bound repositories are available in{" "}
            <WorkspaceReposPopover repos={repos} /> to preload; runs clone any
            other repository on demand.
          </p>
        </div>
        <div className="min-h-0 overflow-auto">
          <Editor
            height="min(60vh, 600px)"
            language="shell"
            value={value}
            onChange={(next) => onChange((next ?? "").replace(/\r\n/g, "\n"))}
            theme={theme === "dark" ? "vs-dark" : "light"}
            options={{
              ariaLabel: label,
              automaticLayout: true,
              minimap: { enabled: false },
              fontSize: 13,
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              tabSize: 2,
            }}
          />
        </div>
        <div className="flex justify-end border-t border-default p-3">
          <DialogClose render={<Button size="sm" />}>Done</DialogClose>
        </div>
      </DialogPopup>
    </Dialog>
  )
}
