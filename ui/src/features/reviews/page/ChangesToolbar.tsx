import { Skeleton } from "@/components/Skeleton"
import { cn } from "@/lib/utils"
import { Button } from "@langchain/macaw-components/Button"
import { XIcon } from "@langchain/macaw-components/icons"
import { Link as MacawLink } from "@langchain/macaw-components/Link"
import { Text } from "@langchain/macaw-components/Text"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ColumnsIcon } from "@phosphor-icons/react/dist/ssr/Columns"
import { RowsIcon } from "@phosphor-icons/react/dist/ssr/Rows"
import { useQuery } from "@tanstack/react-query"
import type { Ref } from "react"

import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { subtleLink } from "@/features/reviews/lib/styles"
import { matchesFileFilter } from "./diffEntries"
import { isLive } from "./pullRequestStanding"
import { reviewQueries, type PullRequestRef } from "./queries"
import { ReadingOrderTabs } from "./ReadingOrderTabs"
import { MarkStepRead, StepHeader } from "./StepSection"
import { useReviewPage } from "./store"
import { plural } from "./text"
import { WalkthroughCallout } from "./WalkthroughCallout"

/** The rule between the overview and the files: what's left to read, and how to read it. */
export function ChangesToolbar({
  pr,
  rowRef,
}: {
  pr: PullRequestRef
  rowRef: Ref<HTMLDivElement>
}) {
  const detail = useQuery(reviewQueries.detail(pr)).data
  const diff = useQuery(reviewQueries.diff(pr))
  const diffStyle = useReviewPage((state) => state.diffStyle)
  const setDiffStyle = useReviewPage((state) => state.setDiffStyle)
  const viewed = useReviewPage((state) => state.viewed)
  const jumpToUnviewed = useReviewPage((state) => state.jumpToUnviewed)
  const walkthrough = useReviewPage((state) =>
    state.order === "guide" ? state.walkthroughToolbar : null
  )
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
    <div className="w-full">
      {detail && steps === 0 && open && files.length > 0 && (
        <div className="mx-space-4 mb-space-4">
          <WalkthroughCallout pr={pr} detail={detail} />
        </div>
      )}
      {diff.data?.truncated && (
        <p className="mx-space-4 mb-space-2 text-xs text-secondary">
          GitHub only returned some of this PR&apos;s files.{" "}
          <MacawLink
            href={githubUrls.pullRequest(pr, "/files")}
            target="_blank"
            rel="noreferrer"
            variant="sm"
          >
            See every file on GitHub
          </MacawLink>
        </p>
      )}
      <div
        ref={rowRef}
        data-review-changes-toolbar
        className="bg-surface-level-1"
      >
        <div className="flex min-h-11.25 flex-wrap items-center gap-x-space-3 gap-y-space-2 border-b border-secondary pr-space-3 pl-space-4">
          <div className="@container flex min-h-6 min-w-0 flex-[1_1_16rem] items-center">
            {walkthrough ? (
              <StepHeader group={walkthrough} inToolbar />
            ) : (
              <div className="flex min-w-0 flex-wrap items-center gap-x-space-3 gap-y-space-1">
                <Text as="h2" variant="sm" weight="semibold" color="primary">
                  Changes
                </Text>
                {diff.data ? (
                  <span className="text-xs text-secondary tabular-nums">
                    {plural(files.length, "file")} ·{" "}
                    {linesLeft === 0 ? (
                      "all lines reviewed"
                    ) : (
                      <Button
                        color="secondary"
                        variant="plain"
                        size="sm"
                        aria-label={`${linesLeft.toLocaleString()} lines left`}
                        type="button"
                        title="Go to the next file you haven't viewed"
                        onClick={jumpToUnviewed}
                        className={cn("h-auto border-0 px-0", subtleLink)}
                      >
                        {linesLeft.toLocaleString()} lines left
                      </Button>
                    )}
                    {viewedCount > 0 && ` · ${viewedCount} viewed`}
                  </span>
                ) : diff.isError ? (
                  <span className="text-xs text-secondary">Unavailable</span>
                ) : (
                  <Skeleton className="h-3 w-32" />
                )}
              </div>
            )}
          </div>
          {diff.data && fileFilter.trim() && (
            <span className="flex items-center gap-space-1 rounded-md bg-brand-subtle py-0.5 pr-0.5 pl-space-1 text-xs text-brand-primary tabular-nums">
              Showing {shownCount} of {files.length} matching “
              {fileFilter.trim()}
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
          <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-space-3">
            {steps > 0 && (
              <ReadingOrderTabs steps={steps} files={files.length} />
            )}
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
            {walkthrough && <MarkStepRead group={walkthrough} />}
          </div>
        </div>
      </div>
    </div>
  )
}
