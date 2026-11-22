import { productAnalytics } from "@/lib/analytics"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { SquareSplitVerticalIcon } from "@phosphor-icons/react/dist/ssr/SquareSplitVertical"
import { useQuery } from "@tanstack/react-query"

import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

/** Open SWE's walkthrough or plain tree order, for both the file list and the diff. */
export function ReadingOrderTabs({
  steps,
  files,
  className,
}: {
  steps: number
  files: number | undefined
  className?: string
}) {
  const order = useReviewPage((state) => state.order)
  const setOrder = useReviewPage((state) => state.setOrder)
  return (
    <GroupedTabs
      size="xs"
      value={order}
      onChange={(value) => {
        productAnalytics.featureOpened(
          value === "guide" ? "walkthrough" : "files"
        )
        setOrder(value)
      }}
      className={className}
      options={[
        {
          value: "guide",
          display: `Walkthrough · ${steps}`,
          tooltip: "Robin's reading order",
        },
        {
          value: "files",
          display: `Files · ${files ?? "–"}`,
          tooltip: "Every file in tree order",
        },
      ]}
    />
  )
}

/** The desktop sidebar's one switch between the PR list, the walkthrough and the files. */
export function NavigatorSwitch({
  pr,
  listShown,
  onShowList,
}: {
  pr: PullRequestRef
  listShown: boolean
  onShowList: () => void
}) {
  const steps =
    useQuery(reviewQueries.detail(pr)).data?.walkthrough?.steps.length ?? 0
  const files = useQuery(reviewQueries.diff(pr)).data?.files.length
  const order = useReviewPage((state) => state.order)
  const setOrder = useReviewPage((state) => state.setOrder)
  const setNavigatorOpen = useReviewPage((state) => state.setNavigatorOpen)
  const stacked = useReviewPage((state) => state.navigatorLayout === "stacked")
  const toggleLayout = useReviewPage((state) => state.toggleNavigatorLayout)
  return (
    <div className="flex h-11 items-center gap-space-2 px-space-3">
      <GroupedTabs
        size="xs"
        value={
          listShown ? "list" : steps > 0 ? (stacked ? "guide" : order) : "files"
        }
        onChange={(value) => {
          if (value === "list") return onShowList()
          if (!stacked) {
            productAnalytics.featureOpened(
              value === "guide" ? "walkthrough" : "files"
            )
            setOrder(value)
          }
          setNavigatorOpen(true)
        }}
        className="flex-1 [&>*]:flex-1 [&>*]:whitespace-nowrap"
        options={[
          { value: "list", display: "PRs", tooltip: "Pull requests" },
          ...(steps > 0 && stacked
            ? [
                {
                  value: "guide" as const,
                  display: "Walkthrough & files",
                  tooltip: "The walkthrough and every file together",
                },
              ]
            : [
                ...(steps > 0
                  ? [
                      {
                        value: "guide" as const,
                        display: `Walkthrough · ${steps}`,
                        tooltip: "Robin's reading order",
                      },
                    ]
                  : []),
                {
                  value: "files" as const,
                  display: `Files · ${files ?? "–"}`,
                  tooltip: "Every file in tree order",
                },
              ]),
        ]}
      />
      {!listShown && steps > 0 && (
        <IconButton
          icon={SquareSplitVerticalIcon}
          label={
            stacked
              ? "Show the walkthrough and files as tabs"
              : "Show the walkthrough and files together"
          }
          aria-pressed={stacked}
          size="sm"
          variant={stacked ? "outlined" : "plain"}
          color={stacked ? "primary" : "secondary"}
          onClick={toggleLayout}
        />
      )}
    </div>
  )
}
