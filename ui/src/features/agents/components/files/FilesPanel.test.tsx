/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { FilesPanel } from "./FilesPanel"

const files: Record<string, string> = {
  "docs/guide.md":
    "![diagram](img/flow.png) ![outside](../logo.png) [next](../notes/next.md) [site](https://example.com) [glossary][g] ![chart][c]\n\n[g]: terms.md\n[c]: img/chart.png",
  "notes/next.md": "[back](../docs/guide.md) [sibling](todo.md)",
  "data/big.csv": `id\n${"1\n".repeat(150_000)}`,
  "data/wide.csv": "c,".repeat(100_000),
  "site/index.html": "<h1>deck</h1>",
  "lab/broken.ipynb": "<html>not a notebook</html>",
  "pics/photo.png": "<html>not an image</html>",
  "lab/run.ipynb": JSON.stringify({
    metadata: { language_info: { name: "python" } },
    cells: [
      { cell_type: "markdown", source: ["# Results"] },
      {
        cell_type: "code",
        execution_count: 3,
        source: "plot()",
        outputs: [
          {
            output_type: "display_data",
            data: {
              "text/html": "<script>parent.x=1</script>",
              "text/plain": "html",
            },
          },
          {
            output_type: "display_data",
            data: {
              "image/svg+xml": ["<svg xmlns='http://www.w3.org/2000/svg'/>"],
            },
          },
          {
            output_type: "error",
            traceback: ["\u001b[0;31mValueError\u001b[0m: bad"],
          },
        ],
      },
    ],
  }),
}

vi.stubGlobal("fetch", async (url: string) => {
  const name = decodeURIComponent(url.split("/").pop() ?? "")
  const path = Object.keys(files).find((key) => key.endsWith(name)) ?? ""
  return new Response(files[path] ?? "", { status: 200 })
})

let minted = 0

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: {
    getThreadPath: async (_thread: string, path: string) => ({
      kind: "file",
      contents: files[path] ?? "",
      binary: false,
      truncated: false,
      size: (files[path] ?? "").length,
    }),
    createThreadFilePreview: async (_thread: string, path: string) => ({
      path: `/threads/t/files/raw/TOKEN${++minted}/${path.split("/").pop()}`,
    }),
  },
}))
vi.mock("./FileBrowserPanel", () => ({ FileBrowserPanel: () => null }))
vi.mock("@/features/agents/components/PullRequestPreview", () => ({
  PreviewablePullRequestLink: (props: React.ComponentProps<"a">) => (
    <a {...props} />
  ),
  PreviewablePullRequestCard: ({ fallback }: { fallback: React.ReactNode }) =>
    fallback,
}))

afterEach(cleanup)

function open(relativePath: string, onOpenFile = vi.fn()) {
  const client = new QueryClient()
  const panel = (path: string, revealRequestId = 0) => (
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <FilesPanel
          target={{ kind: "cloud", threadId: "t" }}
          relativePath={path}
          revealRequestId={revealRequestId}
          onOpenFile={onOpenFile}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  const { rerender } = render(panel(relativePath))
  return {
    onOpenFile,
    show: (path: string, revealRequestId = 0) =>
      rerender(panel(path, revealRequestId)),
  }
}

it("renders Markdown with its images and links resolved beside the document", async () => {
  const { onOpenFile, show } = open("docs/guide.md")

  const image = await screen.findByAltText("diagram")
  expect(image.getAttribute("src")).toMatch(
    /\/dashboard\/api\/threads\/t\/files\/raw\/TOKEN\d+\/img\/flow\.png$/
  )
  expect(screen.getByAltText("chart").getAttribute("src")).toMatch(
    /\/raw\/TOKEN\d+\/img\/chart\.png$/
  )
  expect(screen.getByAltText("outside").getAttribute("src")).toBeNull()
  fireEvent.click(screen.getByRole("link", { name: "next" }))
  expect(onOpenFile).toHaveBeenCalledWith("notes/next.md")
  fireEvent.click(screen.getByRole("link", { name: "glossary" }))
  expect(onOpenFile).toHaveBeenCalledWith("docs/terms.md")
  expect(
    screen.getByRole("link", { name: "site" }).getAttribute("target")
  ).toBe("_blank")

  // Another document resolves against its own folder, not the first one's.
  show("notes/next.md")
  fireEvent.click(await screen.findByRole("link", { name: "sibling" }))
  expect(onOpenFile).toHaveBeenLastCalledWith("notes/todo.md")
})

it("shows a file afresh, images included, when it is opened again", async () => {
  const { show } = open("docs/guide.md")
  const before = (await screen.findByAltText("diagram")).getAttribute("src")

  show("docs/guide.md", 1)

  await waitFor(() =>
    expect(screen.getByAltText("diagram").getAttribute("src")).not.toBe(before)
  )
})

it("renders a CSV with more rows than a function call can take as arguments", async () => {
  open("data/big.csv")

  expect(await screen.findByText("150,000 rows · 1 columns")).toBeTruthy()
})

it("renders only the first columns of a CSV a single line makes very wide", async () => {
  open("data/wide.csv")

  expect(
    await screen.findByText(/100,001 columns · showing the first 500/)
  ).toBeTruthy()
  expect(screen.getAllByRole("columnheader")).toHaveLength(500)
})

it("offers a download when a file's contents don't match its extension", async () => {
  const { show } = open("lab/broken.ipynb")

  expect(await screen.findByText("Not a Jupyter notebook.")).toBeTruthy()
  expect(screen.getByRole("button", { name: "Download" })).toBeTruthy()

  show("pics/photo.png")
  fireEvent.error(await screen.findByAltText("pics/photo.png"))
  expect(await screen.findByText("This file can't be previewed.")).toBeTruthy()
  expect(screen.getByRole("button", { name: "Download" })).toBeTruthy()
})

it("renders notebook outputs without letting their HTML or SVG run as the dashboard", async () => {
  open("lab/run.ipynb")

  expect(await screen.findByRole("heading", { name: "Results" })).toBeTruthy()
  const frame = document.querySelector("iframe")
  expect(frame?.getAttribute("sandbox")).toBe("allow-scripts allow-downloads")
  expect(frame?.getAttribute("srcdoc")).toContain("Content-Security-Policy")
  const svg = screen
    .getAllByAltText("Cell output")
    .find((image) =>
      image.getAttribute("src")?.startsWith("data:image/svg+xml")
    )
  expect(svg?.tagName).toBe("IMG")
})
