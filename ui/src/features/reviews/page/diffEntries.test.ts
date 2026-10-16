// @vitest-environment jsdom

import { expect, it } from "vitest"
import {
  FileDiff,
  getSharedHighlighter,
  renderDiffWithHighlighter,
} from "@pierre/diffs"
import { WorkerPoolManager } from "@pierre/diffs/worker"

import type { ReviewDiffFile } from "@/lib/api"
import { buildEntries } from "./diffEntries"

it("renders a refreshed file without reusing another revision's highlighted rows", async () => {
  const file = (lines: string[], headSha: string): ReviewDiffFile => ({
    path: "server.py",
    previousPath: null,
    baseSha: "base",
    headSha,
    status: "added",
    additions: lines.length,
    deletions: 0,
    patch: [
      "diff --git a/server.py b/server.py",
      "new file mode 100644",
      "--- /dev/null",
      "+++ b/server.py",
      `@@ -0,0 +1,${lines.length} @@`,
      ...lines.map((line) => `+${line}`),
      "",
    ].join("\n"),
  })
  const previous = buildEntries([file(["old = 1"], "old")], null, "files")[0]!
  const current = buildEntries(
    [file(["new = 1", "new += 2"], "new")],
    null,
    "files"
  )[0]!
  const pool = new WorkerPoolManager(
    {
      poolSize: 0,
      workerFactory: () => {
        throw new Error("No workers needed")
      },
    },
    { theme: "pierre-light" }
  )
  await pool.initialize(["python"])
  const highlighter = await getSharedHighlighter({
    themes: ["pierre-light"],
    langs: ["python"],
  })
  const container = document.createElement("diffs-container")
  const view = new FileDiff(
    { diffStyle: "unified", theme: "pierre-light", disableErrorHandling: true },
    pool
  )
  try {
    view.render({ fileDiff: previous.fileDiff, fileContainer: container })
    const options = pool.getDiffRenderOptions()
    pool.inspectCaches().diffCache.set(previous.fileDiff.cacheKey!, {
      options,
      result: renderDiffWithHighlighter(
        previous.fileDiff,
        highlighter,
        options
      ),
    })
    expect(() =>
      view.render({ fileDiff: current.fileDiff, fileContainer: container })
    ).not.toThrow()
    expect(container.shadowRoot?.textContent).toContain("new += 2")
    expect(container.shadowRoot?.textContent).not.toContain("old = 1")
  } finally {
    view.cleanUp()
    pool.terminate()
  }
})
