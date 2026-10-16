import { useMemo } from "react"
import { parseDiffFromFile } from "@pierre/diffs"
import { FileDiff } from "@pierre/diffs/react"
import type { MultiFileDiffProps } from "@pierre/diffs/react"

import { withHunkScopes } from "@/features/agents/utils/hunkScope"

/** `<MultiFileDiff>` that labels each collapsed gap with the scope of the hunk below it. */
export function ScopedFileDiff<LAnnotation = undefined>({
  oldFile,
  newFile,
  ...props
}: MultiFileDiffProps<LAnnotation>) {
  const parseDiffOptions = props.options?.parseDiffOptions
  const fileDiff = useMemo(
    () => withHunkScopes(parseDiffFromFile(oldFile, newFile, parseDiffOptions)),
    [oldFile, newFile, parseDiffOptions]
  )
  return <FileDiff<LAnnotation> fileDiff={fileDiff} {...props} />
}
