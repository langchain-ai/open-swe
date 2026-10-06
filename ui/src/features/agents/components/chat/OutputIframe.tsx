import { useState } from "react"

import type { OutputIframeDisplay } from "@/features/agents/lib/types"
import {
  ARTIFACT_ALLOW,
  ARTIFACT_SANDBOX,
} from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
import { Box, Inline } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { Download } from "@/components/glyphs"

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
      open={expanded}
      onOpenChange={setExpanded}
      render={<section />}
      className="overflow-hidden rounded-panel border border-line bg-panel"
    >
      <Inline
        render={<header />}
        align="center"
        gap="sm"
        className="min-h-row-data px-3"
      >
        <CollapsibleTrigger className="min-w-0 flex-1 cursor-pointer text-label font-medium text-ink">
          <CollapsibleChevron />
          <Box render={<span />} className="truncate">
            {display.title}
          </Box>
        </CollapsibleTrigger>
        {!isLegacy && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Download HTML"
            className="text-ink-subtle hover:text-ink"
            onClick={() => openDownload(display.downloadUrl)}
          >
            <Icon icon={Download} size="sm" />
          </Button>
        )}
      </Inline>
      <CollapsibleContent>
        {isLegacy ? (
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
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}
