import { useState } from "react"
import { CaretDownIcon, CopyIcon } from "@phosphor-icons/react"
import { IoLogoGithub } from "react-icons/io5"

import type { ReviewFinding } from "@/features/reviews/lib/api"
import { Markdown } from "@/components/markdown/Markdown"
import {
  GROUP_STYLES,
  findingClipboardText,
  useExpandedFinding,
} from "@/features/reviews/lib/findings"
import { cn } from "@/lib/utils"

/**
 * The finding rendered inline in the diff (via Pierre's annotation portal). A
 * collapsed header sits at the line; clicking it expands the full details in
 * place. Expand state is shared through context so it survives the annotation
 * remounting as rows window in/out, and so the side panel can drive it.
 */
export function InlineFinding({ finding }: { finding: ReviewFinding }) {
  const { expandedId, reviewUrl, toggle, registerAnnotation } =
    useExpandedFinding()
  const expanded = expandedId === finding.id
  const style = GROUP_STYLES[finding.group]
  const Icon = style.Icon
  return (
    <div
      ref={(node) => registerAnnotation(finding.id, node)}
      className="px-2 py-1 font-sans"
    >
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <button
          type="button"
          onClick={() => toggle(finding)}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} finding: ${finding.title}`}
          className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px]"
        >
          <Icon className={cn("size-3 shrink-0", style.className)} />
          <span className={cn("font-medium", style.className)}>
            {style.label}
          </span>
          <span className="min-w-0 flex-1 truncate text-foreground">
            {finding.title}
          </span>
          {finding.outdated && <Badgeish>Outdated</Badgeish>}
          {finding.status !== "open" && <Badgeish>{finding.status}</Badgeish>}
          <CaretDownIcon
            className={cn(
              "size-3 shrink-0 text-muted-foreground transition-transform",
              !expanded && "-rotate-90"
            )}
          />
        </button>
        {expanded && <FindingDetails finding={finding} reviewUrl={reviewUrl} />}
      </div>
    </div>
  )
}

/**
 * The expandable body + actions of a finding, shared by the inline diff
 * annotation and the side-panel row (non-anchored findings).
 */
export function FindingDetails({
  finding,
  reviewUrl,
}: {
  finding: ReviewFinding
  reviewUrl: string
}) {
  const [copied, setCopied] = useState(false)
  const githubUrl =
    finding.github_review_comment_id !== null
      ? `${reviewUrl}#discussion_r${finding.github_review_comment_id}`
      : null

  const copy = () => {
    void navigator.clipboard
      .writeText(findingClipboardText(finding))
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      })
  }

  return (
    <div className="border-t border-border px-3 py-2.5 font-sans">
      <div className="text-xs text-muted-foreground">
        <Markdown content={finding.description} />
      </div>
      {finding.resolution_note && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Resolution: {finding.resolution_note}
        </p>
      )}
      <div className="mt-2.5 flex items-center gap-2">
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <CopyIcon className="size-3" />
          {copied ? "Copied" : "Copy"}
        </button>
        {githubUrl && (
          <a
            href={githubUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <IoLogoGithub className="size-3" />
            View on GitHub
          </a>
        )}
      </div>
    </div>
  )
}

export function Badgeish({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground capitalize">
      {children}
    </span>
  )
}
