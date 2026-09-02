import { describe, expect, it } from "vitest"

import { toPanelFiles } from "./DiffFilesView"

describe("toPanelFiles", () => {
  it("preserves the GitHub patch when full file contents are unavailable", () => {
    const [file] = toPanelFiles([
      {
        path: "docs.json",
        previousPath: null,
        status: "modified",
        additions: 2,
        deletions: 0,
        originalContent: null,
        modifiedContent: null,
        patch: '@@ -1 +1,2 @@\n {"name":"docs"}\n+{"new":true}',
        unrenderable: true,
      },
    ])

    expect(file).toMatchObject({
      filePath: "docs.json",
      patch: '@@ -1 +1,2 @@\n {"name":"docs"}\n+{"new":true}',
      unrenderable: true,
    })
  })
})
