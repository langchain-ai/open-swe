import { useState } from "react"
import { ChevronDown, Download } from "lucide-react"

import type { OutputIframeDisplay } from "@/features/agents/lib/types"
import {
  ARTIFACT_ALLOW,
  ARTIFACT_SANDBOX,
} from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { cn } from "@/lib/utils"

const IFRAME_HEIGHT = 480

function openDownload(url: string) {
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.rel = "noreferrer"
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

export function OutputIframe({ display }: { display: OutputIframeDisplay }) {
  const [expanded, setExpanded] = useState(true)
  const isLegacy = "html" in display

  return (
    <section className="my-2 overflow-hidden rounded-compact border border-line bg-panel">
      <header className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-ink-subtle transition-transform",
              !expanded && "-rotate-90"
            )}
          />
          <span className="truncate text-label font-medium text-ink">
            {display.title}
          </span>
        </button>
        {!isLegacy && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Download HTML"
            onClick={() => openDownload(display.downloadUrl)}
          >
            <Download />
          </Button>
        )}
      </header>
      {expanded &&
        (isLegacy ? (
          <SandboxedHtmlFrame
            title={display.title}
            html={display.html}
            sandbox={ARTIFACT_SANDBOX}
            allow={ARTIFACT_ALLOW}
            className="border-t border-line bg-canvas"
            style={{ height: IFRAME_HEIGHT }}
          />
        ) : (
          <SandboxedHtmlFrame
            title={display.title}
            src={display.previewUrl}
            sandbox={ARTIFACT_SANDBOX}
            allow={ARTIFACT_ALLOW}
            className="border-t border-line bg-canvas"
            style={{ height: IFRAME_HEIGHT }}
          />
        ))}
    </section>
  )
}
