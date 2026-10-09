import { XIcon } from "@langchain/macaw-components/icons"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ColumnsIcon } from "@phosphor-icons/react/dist/ssr/Columns"
import { RowsIcon } from "@phosphor-icons/react/dist/ssr/Rows"
import { useQuery } from "@tanstack/react-query"

import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { subtleLink } from "@/features/reviews/lib/styles"
import { matchesFileFilter } from "./diffEntries"
import { isLive } from "./pullRequestStanding"
import { reviewQueries, type PullRequestRef } from "./queries"
import { ReadingOrderTabs } from "./ReadingOrderTabs"
import { useReviewPage } from "./store"
import { plural } from "./text"
import { WalkthroughCallout } from "./WalkthroughCallout"

/** The rule between the overview and the files: what's left to read, and how to read it. */
export function ChangesToolbar({ pr }: { pr: PullRequestRef }) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const diff = useQuery(reviewQueries.diff(pr))
  const diffStyle = useReviewPage((state) => state.diffStyle)
  const setDiffStyle = useReviewPage((state) => state.setDiffStyle)
  const viewed = useReviewPage((state) => state.viewed)
  const jumpToUnviewed = useReviewPage((state) => state.jumpToUnviewed)
  const files = diff.data?.files ?? []
  const linesLeft = files
    .filter((file) => !viewed.has(file.path))
    .reduce((total, file) => total + file.additions + file.deletions, 0)
  const viewedCount = files.filter((file) => viewed.has(file.path)).length
  const steps = detail?.walkthrough?.steps.length ?? 0
  const open = detail !== undefined && isLive(detail)
  const fileFilter = useReviewPage((state) => state.fileFilter)
  const setFileFilter = useReviewPage((state) => state.setFileFilter)
  const shownCount = files.filter((file) =>
    matchesFileFilter(file.path, fileFilter)
  ).length

  return (
    <div className="w-full px-4 pb-3">
      {detail && steps === 0 && open && files.length > 0 && (
        <div className="mb-4">
          <WalkthroughCallout pr={pr} detail={detail} />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-default pb-2">
        <h2 className="text-[13px] font-semibold text-primary">Changes</h2>
        {diff.data ? (
          <span className="text-xs text-secondary tabular-nums">
            {plural(files.length, "file")} ·{" "}
            {linesLeft === 0 ? (
              "all lines reviewed"
            ) : (
              <button
                type="button"
                title="Go to the next file you haven't viewed"
                onClick={jumpToUnviewed}
                className={subtleLink}
              >
                {linesLeft.toLocaleString()} lines left
              </button>
            )}
            {viewedCount > 0 && ` · ${viewedCount} viewed`}
          </span>
        ) : (
          <span className="text-xs text-secondary">
            {diff.isError ? "Unavailable" : "Loading…"}
          </span>
        )}
        {diff.data && fileFilter.trim() && (
          <span className="flex items-center gap-1 rounded-md bg-brand-subtle py-0.5 pr-0.5 pl-1.5 text-xs text-brand-primary tabular-nums">
            Showing {shownCount} of {files.length} matching “{fileFilter.trim()}
            ”
            <IconButton
              icon={XIcon}
              label="Clear the file filter"
              size="xxs"
              color="secondary"
              variant="plain"
              onClick={() => setFileFilter("")}
            />
          </span>
        )}
        <span className="flex-1" />
        {steps > 0 && <ReadingOrderTabs steps={steps} files={files.length} />}
        <GroupedTabs
          size="xs"
          value={diffStyle}
          onChange={setDiffStyle}
          options={[
            {
              value: "unified",
              icon: RowsIcon,
              tooltip: "Unified",
              "aria-label": "Unified",
            },
            {
              value: "split",
              icon: ColumnsIcon,
              tooltip: "Split",
              "aria-label": "Split",
            },
          ]}
        />
        <DiffWrapToggle />
      </div>
      {diff.data?.truncated && (
        <p className="mt-2 text-xs text-secondary">
          GitHub only returned some of this PR&apos;s files.{" "}
          <a
            className="underline"
            href={githubUrls.pullRequest(pr, "/files")}
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
