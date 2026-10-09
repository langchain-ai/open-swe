import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { useState } from "react"
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/ssr/DownloadSimple"
import { IconButton } from "@langchain/macaw-components/IconButton"

import type { OutputIframeDisplay } from "@/features/agents/lib/types"
import {
  ARTIFACT_ALLOW,
  ARTIFACT_SANDBOX,
} from "@/features/agents/lib/artifactShell"
import { SandboxedHtmlFrame } from "@/features/agents/components/SandboxedHtmlFrame"
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
    <section className="my-space-2 overflow-hidden rounded-lg border border-default bg-surface-level-2">
      <header className="flex items-center gap-space-2 px-space-3 py-space-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-space-2 rounded-sm text-left focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <CaretDownIcon
            size={14}
            weight="regular"
            aria-hidden
            className={cn(
              "shrink-0 text-icon-secondary transition-transform duration-fast",
              !expanded && "-rotate-90"
            )}
          />
          <span className="truncate text-xs font-medium text-primary">
            {display.title}
          </span>
        </button>
        {!isLegacy && (
          <IconButton
            icon={DownloadSimpleIcon}
            label="Download HTML"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={() => openDownload(display.downloadUrl)}
          />
        )}
      </header>
      {expanded &&
        (isLegacy ? (
          <SandboxedHtmlFrame
            title={display.title}
            html={display.html}
            sandbox={ARTIFACT_SANDBOX}
            allow={ARTIFACT_ALLOW}
            className="border-t border-default bg-surface-level-1"
            style={{ height: IFRAME_HEIGHT }}
          />
        ) : (
          <SandboxedHtmlFrame
            title={display.title}
            src={display.previewUrl}
            sandbox={ARTIFACT_SANDBOX}
            allow={ARTIFACT_ALLOW}
            className="border-t border-default bg-surface-level-1"
            style={{ height: IFRAME_HEIGHT }}
          />
        ))}
    </section>
  )
}
