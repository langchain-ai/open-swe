import { useState } from "react"

import { Button } from "@/components/ui/button"
import { describeBrowserError } from "@/features/agents/browser/browserErrorMessages"

interface Props {
  url: string
  /** Chromium net error code when known, e.g. -105. */
  code: number | null
  /** Chromium error name, e.g. "ERR_NAME_NOT_RESOLVED". */
  description: string
  onReload: () => void
}

/** Theme-aware take on Chromium's "This site can't be reached" page. */
export function BrowserUnreachable({
  url,
  code,
  description,
  onReload,
}: Props) {
  const [showDetails, setShowDetails] = useState(false)
  const host = safeHost(url) ?? url
  const friendly = describeBrowserError(description)
  const errorLabel =
    description.length > 0
      ? description.replace(/^net::/, "")
      : `ERR_${code === null ? "FAILED" : Math.abs(code)}`

  return (
    <div className="relative flex h-full min-h-0 w-full overflow-y-auto bg-background">
      <div className="mx-auto flex w-full max-w-xl flex-1 flex-col px-8 py-12 sm:py-16">
        <svg
          viewBox="0 0 64 64"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          className="mb-6 size-12 text-muted-foreground/70"
          aria-hidden="true"
        >
          <path d="M16 12 L48 12 L48 52 L16 52 Z" />
          <path
            d="M22 22 L42 22 M22 30 L36 30 M22 38 L40 38"
            strokeLinecap="round"
          />
          <path d="M52 8 L12 56" strokeLinecap="round" />
        </svg>
        <h1 className="mb-3 text-2xl leading-tight font-semibold text-foreground">
          This site can&rsquo;t be reached
        </h1>
        <p className="text-sm leading-relaxed text-muted-foreground">
          <span className="font-semibold text-foreground">{host}</span>:{" "}
          {friendly}.
        </p>
        {showDetails ? (
          <div className="mt-6 rounded-lg border border-border bg-muted/40 p-4 text-sm">
            <p className="mb-2 font-medium text-foreground">Try:</p>
            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
              <li>Confirming the dev server is running</li>
              <li>Checking the port and host it listens on</li>
              <li>Checking the proxy and the firewall</li>
            </ul>
          </div>
        ) : null}
        <div className="mt-8 text-xs tracking-wide text-muted-foreground/70 uppercase">
          {errorLabel}
        </div>
        <div className="mt-auto flex items-center gap-2 pt-8">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowDetails((value) => !value)}
          >
            {showDetails ? "Hide details" : "Details"}
          </Button>
          <div className="flex-1" />
          <Button type="button" size="sm" onClick={onReload}>
            Reload
          </Button>
        </div>
      </div>
    </div>
  )
}

function safeHost(url: string): string | null {
  try {
    return new URL(url).host
  } catch {
    return null
  }
}
