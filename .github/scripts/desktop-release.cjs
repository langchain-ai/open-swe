const branch = "release-please--branches--main--components--desktop"
const pendingLabel = "autorelease: pending"
const taggedLabel = "autorelease: tagged"

function isReleasePullRequest(pull, repo) {
  const fullName = `${repo.owner}/${repo.repo}`
  return (
    pull.merged_at &&
    pull.base.ref === "main" &&
    pull.base.repo.full_name === fullName &&
    pull.head.repo?.full_name === fullName &&
    pull.head.ref === branch
  )
}

class DesktopRelease {
  constructor(github, repo) {
    this.github = github
    this.repo = repo
  }

  async findReleasePullRequest(sha) {
    const pulls = await this.github.paginate(
      this.github.rest.repos.listPullRequestsAssociatedWithCommit,
      { ...this.repo, commit_sha: sha, per_page: 100 }
    )
    return pulls.find(
      (pull) =>
        isReleasePullRequest(pull, this.repo) &&
        pull.merge_commit_sha === sha &&
        pull.labels.some(
          (label) => label.name === pendingLabel || label.name === taggedLabel
        )
    )
  }

  async markReleased(pull) {
    await this.github.rest.issues.addLabels({
      ...this.repo,
      issue_number: pull.number,
      labels: [taggedLabel],
    })
    if (pull.labels.some((label) => label.name === pendingLabel)) {
      await this.github.rest.issues.removeLabel({
        ...this.repo,
        issue_number: pull.number,
        name: pendingLabel,
      })
    }
  }

  async hasPublishedAssets(sha) {
    const file = await this.github.rest.repos.getContent({
      ...this.repo,
      path: "desktop/package.json",
      ref: sha,
    })
    const { version } = JSON.parse(
      Buffer.from(file.data.content, "base64").toString()
    )
    if (!/^\d+\.\d+\.\d+$/.test(version))
      throw new Error("Invalid desktop release version")
    const tag = `desktop-v${version}`
    let release, ref
    try {
      ;[{ data: release }, { data: ref }] = await Promise.all([
        this.github.rest.repos.getReleaseByTag({ ...this.repo, tag }),
        this.github.rest.git.getRef({ ...this.repo, ref: `tags/${tag}` }),
      ])
    } catch (error) {
      if (error.status === 404) {
        console.info("Desktop release assets or tag are not published yet", {
          tag,
        })
        return false
      }
      throw error
    }
    if (
      release.draft !== false ||
      release.prerelease !== false ||
      ref.object.sha !== sha
    )
      return false
    const names = new Set(release.assets.map((asset) => asset.name))
    const required = [
      `Open-SWE-${version}.app.zip`,
      "latest-mac.yml",
      "oswe-darwin-arm64.tar.gz",
      "oswe-darwin-x64.tar.gz",
      "oswe-linux-arm64.tar.gz",
      "oswe-linux-x64.tar.gz",
      "oswe-SHA256SUMS",
    ]
    return (
      required.every((name) => names.has(name)) &&
      ["-mac.zip", ".dmg", "-mac.zip.blockmap"].every((suffix) =>
        [...names].some(
          (name) =>
            name.startsWith(`Open-SWE-${version}-`) && name.endsWith(suffix)
        )
      )
    )
  }

  async prepareReleasePullRequests() {
    const pulls = await this.github.paginate(this.github.rest.pulls.list, {
      ...this.repo,
      state: "closed",
      base: "main",
      head: `${this.repo.owner}:${branch}`,
      per_page: 100,
    })
    for (const pull of pulls) {
      if (
        !isReleasePullRequest(pull, this.repo) ||
        !pull.labels.some((label) => label.name === pendingLabel)
      )
        continue
      if (!(await this.hasPublishedAssets(pull.merge_commit_sha))) return false
      await this.markReleased(pull)
    }
    return true
  }
}

module.exports = { DesktopRelease, pendingLabel }
