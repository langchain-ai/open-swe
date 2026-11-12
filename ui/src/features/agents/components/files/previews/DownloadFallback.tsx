import { Component, useState } from "react"
import type { ReactNode } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/ssr/DownloadSimple"
import { toast } from "sonner"

import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import { downloadWorkspaceFile } from "@/features/agents/lib/workspaceFiles"

/** A file whose viewer throws, as one whose contents don't match its extension does, downloads instead. */
export class DownloadOnError extends Component<
  { target: TerminalTarget; relativePath: string; children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: unknown): { error: Error } {
    return {
      error:
        error instanceof Error
          ? error
          : new Error("This file can't be previewed."),
    }
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children
    return (
      <DownloadFallback
        target={this.props.target}
        relativePath={this.props.relativePath}
        message={this.state.error.message}
      />
    )
  }
}

/** For a file with no preview: say so, and offer it as a download. */
export function DownloadFallback(props: {
  target: TerminalTarget
  relativePath: string
  message: string
}) {
  const [downloading, setDownloading] = useState(false)
  const download = () => {
    setDownloading(true)
    downloadWorkspaceFile(props.target, props.relativePath)
      .catch((error: unknown) =>
        toast.error(
          error instanceof Error ? error.message : "Could not download the file"
        )
      )
      .finally(() => setDownloading(false))
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-space-3 px-space-6 text-center">
      <span className="text-xs text-secondary">{props.message}</span>
      <Button
        color="secondary"
        variant="outlined"
        size="sm"
        leftDecorator={DownloadSimpleIcon}
        loading={downloading}
        onClick={download}
      >
        Download
      </Button>
    </div>
  )
}
