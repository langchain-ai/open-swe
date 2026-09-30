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
import { useResolvedTheme } from "@/lib/theme"

export function WorkspaceScriptEditor({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  const theme = useResolvedTheme()
  const lines = value ? value.trimEnd().split("\n").length : 0

  return (
    <Dialog>
      <div className="mt-2 flex items-center gap-2">
        <DialogTrigger render={<Button size="sm" variant="outline" />}>
          Edit {label.toLowerCase()}
        </DialogTrigger>
        <span className="text-xs text-muted-foreground">
          {lines
            ? `${lines} ${lines === 1 ? "line" : "lines"}`
            : "Not configured"}
        </span>
      </div>
      <DialogPopup className="max-w-4xl">
        <div className="space-y-2 border-b border-border p-4">
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>
            Edit the shell script, then choose Save scripts on the settings page
            to apply your changes.
          </DialogDescription>
        </div>
        <div className="min-h-0 overflow-auto">
          <Editor
            height="min(60vh, 600px)"
            language="shell"
            value={value}
            onChange={(next) => onChange(next ?? "")}
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
        <div className="flex justify-end border-t border-border p-3">
          <DialogClose render={<Button size="sm" />}>Done</DialogClose>
        </div>
      </DialogPopup>
    </Dialog>
  )
}
