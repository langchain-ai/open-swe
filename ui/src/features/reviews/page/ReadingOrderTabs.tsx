import { productAnalytics } from "@/lib/analytics"
import { GroupedTabs } from "@langchain/macaw-components/GroupedTabs"

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
