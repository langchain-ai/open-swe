import { useState } from "react"
import { ChevronDown, Download } from "lucide-react"

import type { OutputIframeDisplay } from "@/features/agents/lib/types"
import {
  ARTIFACT_ALLOW,
  ARTIFACT_SANDBOX,
} from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { TooltipIconButton } from "@/components/ui/tooltip-icon-button"
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
    <Collapsible
      className="my-2 overflow-hidden rounded-lg border border-border bg-card"
      open={expanded}
      onOpenChange={setExpanded}
      render={<section />}
    >
      <header className="flex items-center gap-2 px-3 py-2">
        <CollapsibleTrigger className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              !expanded && "-rotate-90"
            )}
          />
          <span className="truncate text-xs font-medium text-foreground">
            {display.title}
          </span>
        </CollapsibleTrigger>
        {!isLegacy && (
          <TooltipIconButton
            label="Download HTML"
            onClick={() => openDownload(display.downloadUrl)}
          >
            <Download />
          </TooltipIconButton>
        )}
      </header>
      <CollapsibleContent>
        {isLegacy ? (
          <SandboxedHtmlFrame
            title={display.title}
            html={display.html}
            sandbox={ARTIFACT_SANDBOX}
            allow={ARTIFACT_ALLOW}
            className="border-t border-border bg-background"
            style={{ height: IFRAME_HEIGHT }}
          />
        ) : (
          <SandboxedHtmlFrame
            title={display.title}
            src={display.previewUrl}
            sandbox={ARTIFACT_SANDBOX}
            allow={ARTIFACT_ALLOW}
            className="border-t border-border bg-background"
            style={{ height: IFRAME_HEIGHT }}
          />
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}
