import { useQuery } from "@tanstack/react-query"
import { ColumnsIcon, RowsIcon } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"
import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"
import { WalkthroughCallout } from "./WalkthroughCallout"

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: ReadonlyArray<{ value: T; label: React.ReactNode; title?: string }>
  onChange: (value: T) => void
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-md border border-border p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={cn(
            "flex h-5 items-center gap-1 rounded-[4px] px-1.5 text-[11px] text-muted-foreground hover:text-foreground",
            value === option.value && "bg-accent text-foreground"
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** The rule between the overview and the files: what's left to read, and how to read it. */
export function ChangesToolbar({ pr }: { pr: PullRequestRef }) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const diff = useQuery(reviewQueries.diff(pr))
  const order = useReviewPage((state) => state.order)
  const setOrder = useReviewPage((state) => state.setOrder)
  const diffStyle = useReviewPage((state) => state.diffStyle)
  const setDiffStyle = useReviewPage((state) => state.setDiffStyle)
  const viewed = useReviewPage((state) => state.viewed)
  const files = diff.data?.files ?? []
  const linesLeft = files
    .filter((file) => !viewed.has(file.path))
    .reduce((total, file) => total + file.additions + file.deletions, 0)
  const viewedCount = files.filter((file) => viewed.has(file.path)).length
  const hasWalkthrough = (detail?.walkthrough?.steps.length ?? 0) > 0
  const open = detail?.pr.state === "open"

  return (
    <div className="mx-auto w-full max-w-[880px] px-6 pb-3">
      {detail && !hasWalkthrough && open && files.length > 2 && (
        <div className="mb-4">
          <WalkthroughCallout pr={pr} detail={detail} />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border pb-2">
        <h2 className="text-[13px] font-semibold text-foreground">Changes</h2>
        {diff.data ? (
          <span className="text-xs text-muted-foreground tabular-nums">
            {files.length} file{files.length === 1 ? "" : "s"} ·{" "}
            {linesLeft === 0 ? "all lines reviewed" : `${linesLeft.toLocaleString()} lines left`}
            {viewedCount > 0 && ` · ${viewedCount} viewed`}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">{diff.isError ? "Unavailable" : "Loading…"}</span>
        )}
        <span className="flex-1" />
        {hasWalkthrough && (
          <Segmented
            label="Order"
            value={order}
            onChange={setOrder}
            options={[
              { value: "guide", label: "Walkthrough", title: "Open SWE's reading order" },
              { value: "files", label: "Files", title: "Every file in tree order" },
            ]}
          />
        )}
        <Segmented
          label="Diff layout"
          value={diffStyle}
          onChange={setDiffStyle}
          options={[
            { value: "unified", label: <RowsIcon className="size-3.5" />, title: "Unified" },
            { value: "split", label: <ColumnsIcon className="size-3.5" />, title: "Split" },
          ]}
        />
        <DiffWrapToggle />
      </div>
      {diff.data?.truncated && (
        <p className="mt-2 text-xs text-muted-foreground">
          GitHub only returned some of this PR&apos;s files.{" "}
          <a
            className="underline"
            href={`https://github.com/${pr.owner}/${pr.repo}/pull/${pr.number}/files`}
            target="_blank"
            rel="noreferrer"
          >
            See every file on GitHub
          </a>
        </p>
      )}
    </div>
  )
}
