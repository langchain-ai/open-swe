import { useCallback } from "react"
import { toast } from "sonner"

import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/** A `javascript:` URL that opens the GitHub PR in the current tab as an Open SWE review. */
export function reviewBookmarkletUrl(origin: string): string {
  // Percent-encoded, the base holds only characters that are inert inside a JS string literal.
  const base = encodeURIComponent(`${new URL(origin).origin}/agents/reviews/`)
  const script = `(()=>{const m=location.href.match(/github\\.com\\/([^/]+)\\/([^/]+)\\/pull\\/(\\d+)/);if(!m){alert("Open this on a GitHub pull request page.");return}window.open(decodeURIComponent("${base}")+m[1]+"/"+m[2]+"/"+m[3],"_blank")})()`
  return `javascript:${script}`
}

/** A link meant to be dragged to the bookmarks bar, not clicked here. */
export function ReviewBookmarklet() {
  // React refuses `javascript:` hrefs in JSX, so the URL is set on the node itself.
  const setBookmarkletHref = useCallback((node: HTMLAnchorElement | null) => {
    node?.setAttribute("href", reviewBookmarkletUrl(window.location.origin))
  }, [])
  return (
    <a
      ref={setBookmarkletHref}
      draggable
      title="Drag to your bookmarks bar, then click it on any GitHub pull request"
      onClick={(event) => {
        event.preventDefault()
        toast.info("Drag this button to your bookmarks bar", {
          description:
            "Then click the bookmark on any GitHub pull request to open its review here.",
        })
      }}
      className={cn(
        buttonVariants({ variant: "outline", size: "sm" }),
        "cursor-grab active:cursor-grabbing"
      )}
    >
      {/* Browsers keep no favicon for a `javascript:` bookmark; the emoji in its name stands in. */}
      👀 Open in Open SWE
    </a>
  )
}
