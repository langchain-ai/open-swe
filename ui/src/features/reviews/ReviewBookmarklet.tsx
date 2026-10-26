import { Button } from "@langchain/macaw-components/Button"
import { useCallback } from "react"
import { toast } from "sonner"

/** Opens a GitHub PR or an agent thread's latest linked PR as an Open SWE review. */
export function reviewBookmarkletUrl(origin: string): string {
  const base = encodeURIComponent(new URL(origin).origin)
  const script = `(()=>{const base=decodeURIComponent("${base}"),page=new URL(location.href);let url=page;if(page.origin===base&&/^\\/agents\\/[0-9a-f-]{36}\\/?$/i.test(page.pathname)){const links=document.querySelectorAll("a[data-thread-pull-request]");if(!links.length){alert("This agent thread has no loaded linked pull request. Wait for it to load, then try again.");return}url=new URL(links[links.length-1].getAttribute("data-thread-pull-request"))}const m=url.hostname==="github.com"&&url.pathname.match(/^\\/([^/]+)\\/([^/]+)\\/pull\\/(\\d+)(?:\\/|$)/);if(!m){alert("Open this on a GitHub pull request or an Open SWE agent thread with a linked pull request.");return}window.open(base+"/agents/reviews/"+m[1]+"/"+m[2]+"/"+m[3],"_blank")})()`
  return `javascript:${script}`
}

/** A link meant to be dragged to the bookmarks bar, not clicked here. */
export function ReviewBookmarklet() {
  // React refuses `javascript:` hrefs in JSX, so the URL is set on the node itself.
  const setBookmarkletHref = useCallback((node: HTMLElement | null) => {
    node?.setAttribute("href", reviewBookmarkletUrl(window.location.origin))
  }, [])
  return (
    <Button
      ref={setBookmarkletHref}
      as={
        <a
          draggable
          title="Drag to your bookmarks bar, then click it on a GitHub pull request or Open SWE agent thread"
        />
      }
      color="secondary"
      variant="outlined"
      size="xs"
      className="cursor-grab active:cursor-grabbing"
      onClick={(event) => {
        event.preventDefault()
        toast.info("Drag this button to your bookmarks bar", {
          description:
            "Then click the bookmark on a GitHub pull request or Open SWE agent thread to open its linked PR review here.",
        })
      }}
    >
      {/* Browsers keep no favicon for a `javascript:` bookmark; the emoji in its name stands in. */}
      👀 Open in Open SWE
    </Button>
  )
}
