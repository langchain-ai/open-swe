import { beforeEach, expect, it, vi } from "vitest"
import { api, type ReviewDiffFile } from "@/lib/api"
import { loadReviewFileContents } from "./fileContents"

vi.mock("@/lib/api", () => ({ api: { getReviewFileContents: vi.fn() } }))

const file: ReviewDiffFile = {
  path: "file.py",
  previousPath: null,
  status: "modified",
  additions: 1,
  deletions: 1,
  position: 0,
  patch: "patch",
  baseSha: "a".repeat(40),
  headSha: "b".repeat(40),
}

beforeEach(() => vi.mocked(api.getReviewFileContents).mockReset())

it("isolates refreshed revisions and sends the displayed snapshot refs", async () => {
  vi.mocked(api.getReviewFileContents).mockResolvedValue({
    originalContent: "old",
    modifiedContent: "new",
  })
  await loadReviewFileContents("snapshot", "repo", 1, file)
  const refreshed = { ...file, headSha: "c".repeat(40) }
  await loadReviewFileContents("snapshot", "repo", 1, refreshed)
  expect(api.getReviewFileContents).toHaveBeenCalledTimes(2)
  expect(api.getReviewFileContents).toHaveBeenLastCalledWith(
    "snapshot",
    "repo",
    1,
    file.path,
    file.path,
    file.baseSha,
    refreshed.headSha
  )
})

it("rejects unavailable contents and permits a retry", async () => {
  vi.mocked(api.getReviewFileContents).mockResolvedValueOnce({
    originalContent: "old",
    modifiedContent: null,
  })
  await expect(
    loadReviewFileContents("unavailable", "repo", 1, file)
  ).rejects.toThrow("unavailable")
  vi.mocked(api.getReviewFileContents).mockResolvedValueOnce({
    originalContent: "old",
    modifiedContent: "new",
  })
  await expect(
    loadReviewFileContents("unavailable", "repo", 1, file)
  ).resolves.toEqual({ originalContent: "old", modifiedContent: "new" })
})
