import { renderAsync } from "docx-preview"

import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import {
  ARTIFACT_CSP,
  withArtifactShell,
} from "@/features/agents/lib/artifactShell"
import type { ViewerProps } from "@/features/agents/lib/filePreview"
import { usePreviewData } from "@/features/agents/lib/workspaceFiles"
import { useResolvedTheme } from "@/lib/theme"
import { queryMessage } from "./PreviewMessage"

/**
 * The document as HTML. It is built in elements never attached to the page,
 * with images inlined as data URLs, and only ever shown inside a sandboxed frame.
 */
async function documentHtml(bytes: ArrayBuffer): Promise<string> {
  const body = document.createElement("div")
  const styles = document.createElement("div")
  await renderAsync(bytes, body, styles, {
    useBase64URL: true,
    ignoreLastRenderedPageBreak: true,
  })
  return `${styles.innerHTML}${body.innerHTML}`
}

/** A Word document, laid out page by page. */
export default function DocumentPreview(props: ViewerProps) {
  const theme = useResolvedTheme()
  const html = usePreviewData(props.url, documentHtml)
  const message = queryMessage(html)
  if (message || html.data === undefined) return message
  return (
    // No scripts: the document needs none, and its links stay inert.
    <SandboxedHtmlFrame
      title={props.path}
      html={withArtifactShell(html.data, theme)}
      csp={ARTIFACT_CSP}
      className="min-h-0 flex-1"
    />
  )
}
