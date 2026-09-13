const MS_PER_DAY = 24 * 60 * 60 * 1000

const DEFAULT_BYPASS_LABEL = "do-not-close"
const DEFAULT_PENDING_DELETION_LABEL = "pending-deletion"
const DEFAULT_WARNING_DAYS = 7
const DEFAULT_CLOSE_DAYS = 14
const DEFAULT_MAX_ITEMS = 1000
const COMMENT_MARKER = "<!-- old-pr-auto-close -->"
const WORKFLOW_BOT_LOGIN = "github-actions[bot]"

function parsePositiveInt(value, fallback, name) {
  if (value === undefined || value === null || value === "") return fallback
  if (!/^\d+$/.test(String(value).trim())) {
    throw new Error(`${name} must be a positive integer, got "${value}"`)
  }
  const parsed = Number.parseInt(value, 10)
  if (parsed <= 0) {
    throw new Error(`${name} must be a positive integer, got "${value}"`)
  }
  return parsed
}

function ageInDays(createdAt, now) {
  const created = new Date(createdAt).getTime()
  if (!Number.isFinite(created)) {
    throw new Error(`Unparseable created date: ${JSON.stringify(createdAt)}`)
  }
  return Math.floor((now.getTime() - created) / MS_PER_DAY)
}

function labelNames(labels) {
  return labels.map((label) => (typeof label === "string" ? label : label.name))
}

async function ensureLabel({ github, owner, repo, name, color, description }) {
  try {
    await github.rest.issues.getLabel({ owner, repo, name })
  } catch (error) {
    if (error.status !== 404) throw error
    try {
      await github.rest.issues.createLabel({
        owner,
        repo,
        name,
        color,
        description,
      })
    } catch (createError) {
      if (createError.status !== 422) throw createError
      await github.rest.issues.getLabel({ owner, repo, name })
    }
  }
}

async function getLivePr({ github, owner, repo, number }) {
  const { data: pr } = await github.rest.pulls.get({
    owner,
    repo,
    pull_number: number,
  })
  return {
    labels: labelNames(pr.labels ?? []),
    state: pr.state,
  }
}

async function addIssueLabel({
  github,
  owner,
  repo,
  issueNumber,
  name,
  labels,
}) {
  if (labels.includes(name)) return
  await github.rest.issues.addLabels({
    owner,
    repo,
    issue_number: issueNumber,
    labels: [name],
  })
  labels.push(name)
}

async function removeIssueLabel({
  github,
  owner,
  repo,
  issueNumber,
  name,
  labels,
}) {
  if (!labels.includes(name)) return false
  try {
    await github.rest.issues.removeLabel({
      owner,
      repo,
      issue_number: issueNumber,
      name,
    })
  } catch (error) {
    if (error.status !== 404) throw error
  }
  labels.splice(labels.indexOf(name), 1)
  return true
}

async function findMarkerComment({ github, owner, repo, issueNumber }) {
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: issueNumber,
    per_page: 100,
  })
  return comments.find(
    (comment) =>
      comment.user?.login === WORKFLOW_BOT_LOGIN &&
      comment.user?.type === "Bot" &&
      comment.body?.includes(COMMENT_MARKER)
  )
}

async function minimizeMarkerComment({
  github,
  core,
  owner,
  repo,
  issueNumber,
}) {
  try {
    const marker = await findMarkerComment({ github, owner, repo, issueNumber })
    if (!marker) return
    await github.graphql(
      `
      mutation($id: ID!) {
        minimizeComment(input: {subjectId: $id, classifier: OUTDATED}) {
          minimizedComment { isMinimized }
        }
      }
    `,
      { id: marker.node_id }
    )
  } catch (error) {
    core.warning(
      `Could not minimize stale warning on PR #${issueNumber}: ${error.message}`
    )
  }
}

async function applyBypassLabel({
  github,
  owner,
  repo,
  issueNumber,
  bypassLabel = DEFAULT_BYPASS_LABEL,
}) {
  const { data: issue } = await github.rest.issues.get({
    owner,
    repo,
    issue_number: issueNumber,
  })
  if ((issue.labels ?? []).some((label) => label.name === bypassLabel))
    return false
  await github.rest.issues.addLabels({
    owner,
    repo,
    issue_number: issueNumber,
    labels: [bypassLabel],
  })
  return true
}

async function clearPendingDeletion({
  github,
  core,
  owner,
  repo,
  issueNumber,
  pendingLabel = DEFAULT_PENDING_DELETION_LABEL,
}) {
  let removed = true
  try {
    await github.rest.issues.removeLabel({
      owner,
      repo,
      issue_number: issueNumber,
      name: pendingLabel,
    })
  } catch (error) {
    if (error.status !== 404) throw error
    removed = false
  }
  await minimizeMarkerComment({ github, core, owner, repo, issueNumber })
  return removed
}

function warningBody({ warningDays, closeDays, bypassLabel }) {
  const noticeDays = closeDays - warningDays
  return [
    COMMENT_MARKER,
    `This PR has been open for at least ${warningDays} days.`,
    "",
    `It will be closed automatically once it has been open for at least ${closeDays} days and this warning is at least ${noticeDays} days old, unless a maintainer applies the \`${bypassLabel}\` label or comments:`,
    "",
    "```",
    "!keep-open",
    "```",
  ].join("\n")
}

function closeBody({ closeDays, bypassLabel }) {
  return [
    COMMENT_MARKER,
    `This PR has been open for at least ${closeDays} days and is being closed automatically.`,
    "",
    `If this work is still active, feel free to reopen it or open a fresh PR. The \`${bypassLabel}\` label (or a maintainer commenting \`!keep-open\`) exempts a PR from this cleanup.`,
  ].join("\n")
}

async function refreshCloseCandidate({
  github,
  core,
  owner,
  repo,
  number,
  bypassLabel,
  pendingDeletionLabel,
}) {
  const live = await getLivePr({ github, owner, repo, number })
  if (live.state === "open" && !live.labels.includes(bypassLabel))
    return live.labels
  await removeIssueLabel({
    github,
    owner,
    repo,
    issueNumber: number,
    name: pendingDeletionLabel,
    labels: live.labels,
  })
  if (live.labels.includes(bypassLabel)) {
    await minimizeMarkerComment({
      github,
      core,
      owner,
      repo,
      issueNumber: number,
    })
  }
  return null
}

async function searchOpenPrs({ github, owner, repo, maxItems, core }) {
  const items = []
  let incomplete = false
  try {
    for await (const response of github.paginate.iterator(
      github.rest.search.issuesAndPullRequests,
      {
        q: `repo:${owner}/${repo} is:pr is:open`,
        per_page: 100,
        sort: "created",
        order: "asc",
      }
    )) {
      incomplete ||= response.data.incomplete_results === true
      for (const item of response.data) {
        items.push(item)
        if (items.length >= maxItems) {
          core.warning(
            `Reached maxItems cap (${maxItems}); some open PRs were not processed.`
          )
          return { items, incomplete }
        }
      }
    }
  } catch (error) {
    core.warning(
      `PR search failed after ${items.length} result(s): ${error.message}`
    )
    incomplete = true
  }
  return { items, incomplete }
}

async function processPr({
  github,
  core,
  owner,
  repo,
  item,
  now,
  bypassLabel,
  pendingDeletionLabel,
  warningDays,
  closeDays,
}) {
  const number = item.number
  const age = ageInDays(item.created_at, now)
  if (age < warningDays) return "skipped"

  const labels = await refreshCloseCandidate({
    github,
    core,
    owner,
    repo,
    number,
    bypassLabel,
    pendingDeletionLabel,
  })
  if (labels === null) return "skipped"

  const existing = await findMarkerComment({
    github,
    owner,
    repo,
    issueNumber: number,
  })
  if (!existing) {
    await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: number,
      body: warningBody({ warningDays, closeDays, bypassLabel }),
    })
    const refreshedLabels = await refreshCloseCandidate({
      github,
      core,
      owner,
      repo,
      number,
      bypassLabel,
      pendingDeletionLabel,
    })
    if (refreshedLabels === null) return "skipped"
    await addIssueLabel({
      github,
      owner,
      repo,
      issueNumber: number,
      name: pendingDeletionLabel,
      labels: refreshedLabels,
    })
    return "warned"
  }

  const warningAge = ageInDays(existing.created_at, now)
  if (age >= closeDays && warningAge >= closeDays - warningDays) {
    const refreshedLabels = await refreshCloseCandidate({
      github,
      core,
      owner,
      repo,
      number,
      bypassLabel,
      pendingDeletionLabel,
    })
    if (refreshedLabels === null) return "skipped"
    await github.rest.issues.updateComment({
      owner,
      repo,
      comment_id: existing.id,
      body: closeBody({ closeDays, bypassLabel }),
    })
    await github.rest.pulls.update({
      owner,
      repo,
      pull_number: number,
      state: "closed",
    })
    await removeIssueLabel({
      github,
      owner,
      repo,
      issueNumber: number,
      name: pendingDeletionLabel,
      labels: refreshedLabels,
    })
    return "closed"
  }

  await addIssueLabel({
    github,
    owner,
    repo,
    issueNumber: number,
    name: pendingDeletionLabel,
    labels,
  })
  return "skipped"
}

async function run({ github, context, core, options = {} }) {
  const { owner, repo } = context.repo
  const bypassLabel =
    options.bypassLabel || process.env.BYPASS_LABEL || DEFAULT_BYPASS_LABEL
  const pendingDeletionLabel =
    options.pendingDeletionLabel ||
    process.env.PENDING_DELETION_LABEL ||
    DEFAULT_PENDING_DELETION_LABEL
  const warningDays = parsePositiveInt(
    options.warningDays ?? process.env.WARNING_DAYS,
    DEFAULT_WARNING_DAYS,
    "warningDays"
  )
  const closeDays = parsePositiveInt(
    options.closeDays ?? process.env.CLOSE_DAYS,
    DEFAULT_CLOSE_DAYS,
    "closeDays"
  )
  const maxItems = parsePositiveInt(
    options.maxItems ?? process.env.MAX_ITEMS,
    DEFAULT_MAX_ITEMS,
    "maxItems"
  )
  if (warningDays >= closeDays) {
    throw new Error(
      `warningDays (${warningDays}) must be less than closeDays (${closeDays})`
    )
  }

  await ensureLabel({
    github,
    owner,
    repo,
    name: bypassLabel,
    color: "0e8a16",
    description: "Bypass automatic closure of old PRs",
  })
  await ensureLabel({
    github,
    owner,
    repo,
    name: pendingDeletionLabel,
    color: "fbca04",
    description: "PR is past the auto-close warning threshold",
  })

  const { items, incomplete } = await searchOpenPrs({
    github,
    owner,
    repo,
    maxItems,
    core,
  })
  const summary = { checked: 0, warned: 0, closed: 0, skipped: 0, errors: [] }
  for (const item of items) {
    summary.checked += 1
    try {
      const result = await processPr({
        github,
        core,
        owner,
        repo,
        item,
        now: options.now ?? new Date(),
        bypassLabel,
        pendingDeletionLabel,
        warningDays,
        closeDays,
      })
      summary[result] += 1
    } catch (error) {
      core.warning(`PR #${item.number} failed: ${error.stack ?? error.message}`)
      summary.errors.push(`#${item.number}: ${error.message}`)
    }
  }

  core.info(
    `Checked ${summary.checked}; warned ${summary.warned}; closed ${summary.closed}; ` +
      `skipped ${summary.skipped}; errors ${summary.errors.length}`
  )
  if (incomplete) summary.errors.unshift("PR search did not complete")
  if (summary.errors.length > 0) core.setFailed(summary.errors.join("; "))
  return summary
}

module.exports = {
  run,
  applyBypassLabel,
  clearPendingDeletion,
  warningBody,
  closeBody,
  ageInDays,
  COMMENT_MARKER,
  DEFAULT_BYPASS_LABEL,
  DEFAULT_PENDING_DELETION_LABEL,
}
