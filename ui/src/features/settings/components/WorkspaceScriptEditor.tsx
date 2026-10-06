import { useId, type ReactNode } from "react"
import Editor from "@monaco-editor/react"

import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@langchain/gtm-platform-design-system/ui/dialog"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/gtm-platform-design-system/ui/popover"
import { useResolvedTheme } from "@/lib/theme"

function WorkspaceReposPopover({ repos }: { repos: string[] }) {
  const titleId = useId()
  return (
    <Popover>
      <PopoverTrigger className="cursor-pointer rounded-tick font-mono underline decoration-dotted underline-offset-4 hover:text-ink focus-visible:outline-2 focus-visible:outline-primary">
        OPENSWE_WORKSPACE_REPOS
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-labelledby={titleId}
        className="w-96 max-w-(--available-width)"
      >
        <Stack gap="sm">
          <Box
            render={<h2 id={titleId} />}
            className="text-label font-medium text-ink"
          >
            Expanded value
          </Box>
          <Box
            render={<pre />}
            padding="md"
            bg="muted"
            radius="compact"
            className="max-h-60 overflow-auto font-mono text-label break-all whitespace-pre-wrap"
          >
            <code>{`OPENSWE_WORKSPACE_REPOS="${repos.join(" ")}"`}</code>
          </Box>
        </Stack>
      </PopoverContent>
    </Popover>
  )
}

/** A field whose control is the script dialog's trigger rather than an input. */
export function ScriptField({
  label,
  help,
  children,
}: {
  label: string
  help: string
  children: ReactNode
}) {
  return (
    <Stack gap="sm">
      <Stack gap="xs">
        <Box render={<span />} className="text-label font-medium text-ink">
          {label}
        </Box>
        <Box render={<span />} className="text-meta text-ink-subtle">
          {help}
        </Box>
      </Stack>
      {children}
    </Stack>
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
      <Inline gap="sm" align="center">
        <DialogTrigger render={<Button size="compact" variant="outline" />}>
          Edit {label.toLowerCase()}
        </DialogTrigger>
        <Box render={<span />} className="text-meta text-ink-subtle">
          {lines
            ? `${lines} ${lines === 1 ? "line" : "lines"}`
            : "Not configured"}
        </Box>
      </Inline>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
          <Box render={<p />} className="text-meta text-ink-subtle">
            Bound repositories are available in{" "}
            <WorkspaceReposPopover repos={repos} /> to preload; runs clone any
            other repository on demand.
          </Box>
        </DialogHeader>
        <Box border="line" radius="compact" className="min-h-0 overflow-hidden">
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
        </Box>
        <DialogFooter>
          <DialogClose render={<Button />}>Done</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
