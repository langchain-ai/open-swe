// @vitest-environment jsdom

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { Toaster } from "@/components/Toaster"
import { buildEntries } from "./diffEntries"
import { EntriesContext } from "./entries"
import { FileHeader } from "./FileHeader"

afterEach(() => {
  vi.unstubAllGlobals()
})

it("copies the file path without showing a toast or moving keyboard focus", async () => {
  const path = "src/components/Example.tsx"
  const entries = buildEntries(
    [
      {
        baseSha: "a".repeat(40),
        headSha: "b".repeat(40),
        path,
        previousPath: null,
        status: "added",
        additions: 1,
        deletions: 0,
        patch:
          "diff --git a/src/components/Example.tsx b/src/components/Example.tsx\nnew file mode 100644\n--- /dev/null\n+++ b/src/components/Example.tsx\n@@ -0,0 +1 @@\n+export const example = true\n",
      },
    ],
    null,
    "files"
  )
  const writeText = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal("navigator", { clipboard: { writeText } })
  const client = new QueryClient()
  client.setQueryData(["branding", ""], null)
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <EntriesContext.Provider
          value={new Map(entries.map((entry) => [entry.id, entry]))}
        >
          <FileHeader
            pr={{ owner: "example", repo: "demo", number: 1 }}
            id={entries[0]!.id}
          />
          <Toaster />
        </EntriesContext.Provider>
      </TooltipProvider>
    </QueryClientProvider>
  )
  const copy = screen.getByRole("button", { name: `Copy ${path}` })
  copy.focus()

  await act(async () => {
    fireEvent.click(copy)
  })

  expect(writeText).toHaveBeenCalledWith(path)
  expect(screen.queryByText("Copied the path")).toBeNull()
  expect(document.activeElement).toBe(copy)
})
