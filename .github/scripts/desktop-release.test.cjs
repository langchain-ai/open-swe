const assert = require("node:assert/strict")
const { test } = require("node:test")
const { DesktopRelease } = require("./desktop-release.cjs")

const repo = { owner: "langchain-ai", repo: "open-swe" }
const sha = "release-merge-sha"
const pull = {
  number: 123,
  merged_at: "2026-10-22T00:00:00Z",
  merge_commit_sha: sha,
  base: { ref: "main", repo: { full_name: "langchain-ai/open-swe" } },
  head: {
    ref: "release-please--branches--main--components--desktop",
    repo: { full_name: "langchain-ai/open-swe" },
  },
  labels: [{ name: "autorelease: pending" }],
}

const assets = [
  "Open-SWE-0.3.9.app.zip",
  "latest-mac.yml",
  "Open-SWE-0.3.9-arm64-mac.zip",
  "Open-SWE-0.3.9-arm64-mac.dmg",
  "Open-SWE-0.3.9-arm64-mac.zip.blockmap",
  "oswe-darwin-arm64.tar.gz",
  "oswe-darwin-x64.tar.gz",
  "oswe-linux-arm64.tar.gz",
  "oswe-linux-x64.tar.gz",
  "oswe-SHA256SUMS",
].map((name) => ({ name }))

function client(pulls) {
  const labels = new Set(pulls[0]?.labels.map((label) => label.name))
  return {
    labels,
    paginate: async () => pulls,
    rest: {
      repos: {
        listPullRequestsAssociatedWithCommit: () => {},
        getContent: async () => ({
          data: {
            content: Buffer.from('{"version":"0.3.9"}').toString("base64"),
          },
        }),
        getReleaseByTag: async () => ({
          data: { draft: false, prerelease: false, assets },
        }),
      },
      git: { getRef: async () => ({ data: { object: { sha } } }) },
      pulls: { list: () => {} },
      issues: {
        addLabels: async (args) =>
          args.labels.forEach((label) => labels.add(label)),
        removeLabel: async (args) => labels.delete(args.name),
      },
    },
  }
}

test("publishes only the merged same-repository release PR at the exact commit", async () => {
  assert.equal(
    await new DesktopRelease(client([pull]), repo).findReleasePullRequest(sha),
    pull
  )
  for (const other of [
    { ...pull, merged_at: null },
    { ...pull, merge_commit_sha: "another-commit" },
    { ...pull, labels: [] },
    { ...pull, head: { ...pull.head, ref: "ordinary-feature" } },
    { ...pull, head: { ...pull.head, repo: { full_name: "fork/open-swe" } } },
    { ...pull, base: { ...pull.base, ref: "prod" } },
  ]) {
    assert.equal(
      await new DesktopRelease(client([other]), repo).findReleasePullRequest(
        sha
      ),
      undefined
    )
  }
})

test("unblocks release maintenance only after verifying the complete published release", async () => {
  const published = client([pull])
  assert.equal(
    await new DesktopRelease(published, repo).prepareReleasePullRequests(),
    true
  )
  assert.deepEqual([...published.labels], ["autorelease: tagged"])
  for (const release of [
    { draft: true, prerelease: false, assets },
    { draft: false, prerelease: true, assets },
    { draft: false, prerelease: false, assets: assets.slice(0, -1) },
  ]) {
    const incomplete = client([pull])
    incomplete.rest.repos.getReleaseByTag = async () => ({ data: release })
    assert.equal(
      await new DesktopRelease(incomplete, repo).prepareReleasePullRequests(),
      false
    )
    assert.deepEqual([...incomplete.labels], ["autorelease: pending"])
  }
  const wrongCommit = client([pull])
  wrongCommit.rest.git.getRef = async () => ({
    data: { object: { sha: "different" } },
  })
  assert.equal(
    await new DesktopRelease(wrongCommit, repo).prepareReleasePullRequests(),
    false
  )
  assert.deepEqual([...wrongCommit.labels], ["autorelease: pending"])
})

test("failed API calls and incomplete publications cannot unblock the release queue", async () => {
  for (const status of [404, 403]) {
    const failed = client([pull])
    failed.rest.repos.getReleaseByTag = async () => {
      throw Object.assign(new Error("GitHub failure"), { status })
    }
    if (status === 404)
      assert.equal(
        await new DesktopRelease(failed, repo).prepareReleasePullRequests(),
        false
      )
    else
      await assert.rejects(
        new DesktopRelease(failed, repo).prepareReleasePullRequests(),
        /GitHub failure/
      )
    assert.deepEqual([...failed.labels], ["autorelease: pending"])
  }
  const failed = client([pull])
  failed.rest.issues.addLabels = async () => {
    throw new Error("GitHub unavailable")
  }
  await assert.rejects(
    new DesktopRelease(failed, repo).prepareReleasePullRequests(),
    /GitHub unavailable/
  )
  assert.deepEqual([...failed.labels], ["autorelease: pending"])
})

test("unrelated PRs are untouched, and completed finalization is retryable", async () => {
  const unrelated = client([
    { ...pull, head: { ...pull.head, ref: "ordinary-feature" } },
  ])
  assert.equal(
    await new DesktopRelease(unrelated, repo).prepareReleasePullRequests(),
    true
  )
  assert.deepEqual([...unrelated.labels], ["autorelease: pending"])
  const published = client([
    { ...pull, labels: [{ name: "autorelease: tagged" }] },
  ])
  assert.equal(
    await new DesktopRelease(published, repo).prepareReleasePullRequests(),
    true
  )
  assert.deepEqual([...published.labels], ["autorelease: tagged"])
})
