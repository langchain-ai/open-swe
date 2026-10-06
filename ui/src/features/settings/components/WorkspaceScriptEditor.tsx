import Editor from "@monaco-editor/react"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Dialog, DialogClose, DialogDescription, DialogContent, DialogTitle, DialogTrigger } from "@langchain/gtm-platform-design-system/ui/dialog"
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@langchain/gtm-platform-design-system/ui/popover"
import { useResolvedTheme } from "@/lib/theme"

function WorkspaceReposPopover({ repos }: { repos: string[] }) {
  return (
    <Popover>
      <PopoverTrigger className="cursor-pointer rounded-tick font-mono underline decoration-dotted underline-offset-4 hover:text-ink focus-visible:outline-2 focus-visible:outline-primary">
        OPENSWE_WORKSPACE_REPOS
      </PopoverTrigger>
      <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)]">
        <PopoverTitle>Expanded value</PopoverTitle>
        <pre className="mt-2 max-h-60 overflow-auto rounded-badge bg-muted p-3 font-mono text-label break-all whitespace-pre-wrap">
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
  const theme = useResolvedTheme()
  const lines = value ? value.trimEnd().split("\n").length : 0

  return (
    <Dialog>
      <div className="mt-2 flex items-center gap-2">
        <DialogTrigger render={<Button size="compact" variant="outline" />}>
          Edit {label.toLowerCase()}
        </DialogTrigger>
        <span className="text-meta text-ink-subtle">
          {lines
            ? `${lines} ${lines === 1 ? "line" : "lines"}`
            : "Not configured"}
        </span>
      </div>
      <DialogContent className="max-w-4xl">
        <div className="space-y-2 border-b border-line p-4">
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
          <p className="text-body text-ink-subtle">
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
        <div className="flex justify-end border-t border-line p-3">
          <DialogClose render={<Button size="compact" />}>Done</DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  )
}
