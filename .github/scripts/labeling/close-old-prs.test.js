const assert = require("node:assert/strict")
const test = require("node:test")

const { run, ageInDays, closeBody, warningBody } = require("./close-old-prs.js")

function fixture({ createdAt, comments = [], labels = [] }) {
  const calls = []
  const github = {
    graphql: async () => {},
    paginate: Object.assign(async () => comments, {
      iterator: async function* () {
        yield {
          data: Object.assign([{ number: 1, created_at: createdAt }], {
            incomplete_results: false,
          }),
        }
      },
    }),
    rest: {
      search: {
        issuesAndPullRequests: async () => {},
      },
      issues: {
        addLabels: async (input) => calls.push(["addLabels", input]),
        createComment: async (input) => calls.push(["createComment", input]),
        createLabel: async (input) => calls.push(["createLabel", input]),
        getLabel: async () => {},
        get: async () => ({
          data: { labels: labels.map((name) => ({ name })) },
        }),
        listComments: async () => {},
        removeLabel: async (input) => calls.push(["removeLabel", input]),
        updateComment: async (input) => calls.push(["updateComment", input]),
      },
      pulls: {
        get: async () => ({
          data: { state: "open", labels: labels.map((name) => ({ name })) },
        }),
        update: async (input) => calls.push(["updatePull", input]),
      },
    },
  }
  const core = {
    info: () => {},
    warning: (message) => calls.push(["warning", message]),
    setFailed: (message) => calls.push(["setFailed", message]),
  }
  return { calls, core, github }
}

const context = { repo: { owner: "langchain-ai", repo: "open-swe" } }
const now = new Date("2026-09-10T00:00:00Z")

test("calculates PR age in whole days", () => {
  assert.equal(ageInDays("2026-08-26T12:00:00Z", now), 14)
})

test("warns at 14 days and marks the PR pending deletion", async () => {
  const { calls, core, github } = fixture({ createdAt: "2026-08-26T00:00:00Z" })

  const summary = await run({ github, context, core, options: { now } })

  assert.equal(summary.warned, 1)
  assert.ok(calls.some(([name]) => name === "createComment"))
  assert.ok(
    calls.some(
      ([name, input]) =>
        name === "addLabels" && input.labels.includes("pending-deletion")
    )
  )
})

test("closes at 30 days after a full warning period", async () => {
  const body = warningBody({
    warningDays: 14,
    closeDays: 30,
    bypassLabel: "do-not-close",
  })
  const { calls, core, github } = fixture({
    createdAt: "2026-08-11T00:00:00Z",
    comments: [
      {
        id: 10,
        created_at: "2026-08-25T00:00:00Z",
        body,
        user: { login: "github-actions[bot]", type: "Bot" },
      },
    ],
    labels: ["pending-deletion"],
  })

  const summary = await run({ github, context, core, options: { now } })

  assert.equal(summary.closed, 1)
  assert.ok(
    calls.some(
      ([name, input]) => name === "updatePull" && input.state === "closed"
    )
  )
  assert.ok(
    calls.some(
      ([name, input]) =>
        name === "updateComment" &&
        input.body ===
          closeBody({
            closeDays: 30,
            bypassLabel: "do-not-close",
          })
    )
  )
})

test("does not warn PRs with do-not-close", async () => {
  const { calls, core, github } = fixture({
    createdAt: "2026-08-11T00:00:00Z",
    labels: ["do-not-close"],
  })

  const summary = await run({ github, context, core, options: { now } })

  assert.equal(summary.skipped, 1)
  assert.ok(!calls.some(([name]) => name === "createComment"))
  assert.ok(!calls.some(([name]) => name === "updatePull"))
})
