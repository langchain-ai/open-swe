import { useQuery } from "@tanstack/react-query"

import type { FileCoverage } from "@/lib/api"
import { reviewQueries, type PullRequestRef } from "./queries"
import { Tag } from "./Tag"
import { plural } from "./text"

function describe(file: FileCoverage): {
  label: string
  color: "success" | "warning" | "secondary"
} | null {
  if (file.state === "owner_reviewed")
    return {
      label: file.approved_by.length
        ? `Approved by ${file.approved_by.join(", ")}`
        : "Owners reviewed",
      color: "success",
    }
  if (file.hard)
    return { label: `Needs ${file.code_owners.join(", ")}`, color: "warning" }
  if (file.state === "reviewed")
    return { label: "Reviewed", color: "secondary" }
  if (file.state === "partial")
    return {
      label: `${Math.floor((100 * file.reviewed_lines) / file.changed_lines)}% reviewed`,
      color: "secondary",
    }
  return null
}

/** Who reviewed a file's changed lines, and whether the code owners it needs have. */
export function CoverageTag({
  pr,
  path,
  className,
}: {
  pr: PullRequestRef
  path: string
  className?: string
}) {
  const file = useQuery({
    ...reviewQueries.coverage(pr),
    select: (coverage) => coverage.files.find((item) => item.path === path),
  }).data
  const shown = file ? describe(file) : null
  if (!file || !shown) return null
  const who = file.reviewers.map(
    (reviewer) =>
      `${reviewer.login}: ${plural(reviewer.lines, "line")} of ${file.changed_lines}`
  )
  const title = [
    file.hard
      ? `Hard-required review by ${file.code_owners.join(", ")}`
      : file.code_owners.length
        ? `Owned by ${file.code_owners.join(", ")}`
        : "No code owners",
    ...who,
  ].join("\n")
  return (
    <Tag color={shown.color} title={title} className={className}>
      {shown.label}
    </Tag>
  )
}
