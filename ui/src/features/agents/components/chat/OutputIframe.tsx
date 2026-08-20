import { useState } from "react"
import { ChevronDown, Download } from "lucide-react"

import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import type { OutputIframeDisplay } from "@/features/agents/lib/types"
import { IconButton } from "@/components/ui/button"
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
    <section className="my-2 overflow-hidden rounded-lg border border-border bg-card">
      <header className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              !expanded && "-rotate-90"
            )}
          />
          <span className="truncate text-xs font-medium text-foreground">
            {display.title}
          </span>
        </button>
        {!isLegacy && (
          <IconButton
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Download HTML"
            onClick={() => openDownload(display.downloadUrl)}
          >
            <Download />
          </IconButton>
        )}
      </header>
      {expanded &&
        (isLegacy ? (
          <SandboxedHtmlFrame
            title={display.title}
            html={display.html}
            sandbox="allow-scripts allow-downloads"
            allow="clipboard-write"
            className="border-t border-border bg-white"
            style={{ height: IFRAME_HEIGHT }}
          />
        ) : (
          <SandboxedHtmlFrame
            title={display.title}
            src={display.previewUrl}
            sandbox="allow-scripts allow-downloads"
            allow="clipboard-write"
            className="border-t border-border bg-white"
            style={{ height: IFRAME_HEIGHT }}
          />
        ))}
    </section>
  )
}
