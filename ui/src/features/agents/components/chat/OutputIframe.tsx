import { useEffect, useMemo, useRef, useState } from "react"
import { ChevronDown, Download, ExternalLink } from "lucide-react"

import type { OutputIframeDisplay } from "@/features/agents/lib/types"
import { IconButton } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const MIN_HEIGHT = 180
const MAX_HEIGHT = 720

const HEIGHT_REPORTER = `<script>
(function () {
  function reportHeight() {
    var body = document.body;
    var root = document.documentElement;
    var height = Math.max(
      body ? body.scrollHeight : 0,
      body ? body.offsetHeight : 0,
      root ? root.scrollHeight : 0,
      root ? root.offsetHeight : 0
    );
    window.parent.postMessage({ type: "output-iframe-height", height: height }, "*");
  }
  window.addEventListener("load", reportHeight);
  window.addEventListener("resize", reportHeight);
  if (document.body && typeof MutationObserver !== "undefined") {
    new MutationObserver(reportHeight).observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true
    });
  }
  reportHeight();
})();
<\/script>`

function withHeightReporter(html: string): string {
  const bodyClose = html.search(/<\/body\s*>/i)
  if (bodyClose === -1) return `${html}${HEIGHT_REPORTER}`
  return `${html.slice(0, bodyClose)}${HEIGHT_REPORTER}${html.slice(bodyClose)}`
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

function downloadBlob(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/html" }))
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function OutputIframe({ display }: { display: OutputIframeDisplay }) {
  const [expanded, setExpanded] = useState(true)
  const [height, setHeight] = useState(300)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const srcDoc = useMemo(() => withHeightReporter(display.html), [display.html])

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (
        event.source !== iframeRef.current?.contentWindow ||
        !event.data ||
        typeof event.data !== "object" ||
        event.data.type !== "output-iframe-height" ||
        typeof event.data.height !== "number" ||
        !Number.isFinite(event.data.height)
      ) {
        return
      }
      setHeight(Math.min(Math.max(event.data.height, MIN_HEIGHT), MAX_HEIGHT))
    }
    window.addEventListener("message", handleMessage)
    return () => window.removeEventListener("message", handleMessage)
  }, [])

  const openInNewTab = () => {
    const escaped = escapeHtmlAttribute(display.html)
    const wrapper = `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtmlAttribute(display.title)}</title><style>html,body,iframe{box-sizing:border-box;width:100%;height:100%;margin:0;border:0}body{background:#fff}</style></head><body><iframe sandbox="allow-scripts allow-downloads" allow="clipboard-write" referrerpolicy="no-referrer" srcdoc="${escaped}"></iframe></body></html>`
    const url = URL.createObjectURL(new Blob([wrapper], { type: "text/html" }))
    window.open(url, "_blank", "noopener,noreferrer")
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }

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
        <IconButton
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Download HTML"
          onClick={() => downloadBlob(display.html, display.filename)}
        >
          <Download />
        </IconButton>
        <IconButton
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Open in new tab"
          onClick={openInNewTab}
        >
          <ExternalLink />
        </IconButton>
      </header>
      {expanded && (
        <iframe
          ref={iframeRef}
          title={display.title}
          srcDoc={srcDoc}
          sandbox="allow-scripts allow-downloads"
          allow="clipboard-write"
          referrerPolicy="no-referrer"
          className="block w-full border-0 border-t border-border bg-white"
          style={{ height }}
        />
      )}
    </section>
  )
}
