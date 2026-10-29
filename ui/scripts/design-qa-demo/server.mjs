// Local-only demo server. Never imported by the app or production backend.
import http from "node:http"
import { existsSync, readFileSync, statSync, createReadStream } from "node:fs"
import { resolve, dirname, extname, sep } from "node:path"
import { fileURLToPath } from "node:url"
import { randomUUID } from "node:crypto"
import {
  demoRepo,
  demoPullRequests,
  checkpoints,
  snapshot,
  makeRun,
  ids,
  revision,
  date,
} from "./fixtures.mjs"

const directory = dirname(fileURLToPath(import.meta.url))
const root = resolve(directory, "../../.output/public")
const port = Number(process.argv[2] ?? 4189)
if (!existsSync(resolve(root, "_shell.html")))
  throw new Error("Build the UI first: pnpm --dir ui build")
if (!Number.isInteger(port) || port < 1024 || port > 65535)
  throw new Error("Choose a port between 1024 and 65535")
const api = "/dashboard/api"
const assets = resolve(directory, "assets")
for (const { id } of checkpoints)
  for (const side of ["base", "head"])
    if (!existsSync(resolve(assets, `${id}-${side}.png`)))
      throw new Error(`Missing demo fixture: ${id}-${side}.png. See README.md.`)
const metrics = JSON.parse(
  readFileSync(resolve(assets, "metrics.json"), "utf8")
)
const streams = new Set()
const jobs = new Set()
const runs = new Map()
function reset() {
  for (const job of jobs) clearTimeout(job)
  jobs.clear()
  runs.clear()
  for (const pr of demoPullRequests) {
    if (pr.unreviewed) {
      runs.set(pr.number, [])
      continue
    }
    const run = makeRun(
      pr.number,
      metrics,
      pr.blocked ? ids.blocked : ids.run,
      `http://127.0.0.1:${port}`
    )
    run.report.scenarios = run.report.scenarios.filter((state) =>
      pr.states.includes(state.id)
    )
    if (pr.stale) run.head_sha = "c".repeat(40)
    if (pr.number === 43397)
      run.report.summary =
        "Scripted recap: a subtle button color change and updated hover and pressed feedback. Inspect text contrast in the hover state."
    if (pr.number === 43398)
      run.report.summary =
        "Scripted recap: the action is 2 px taller. Evidence is from an earlier commit; compare the edge, then rerun on the latest revision."
    if (pr.number === 43401)
      run.report.summary =
        "Scripted recap: review button height and fill alignment with the search control."
    if (pr.blocked) {
      Object.assign(run, {
        status: "blocked",
        auth_mode: "dummy",
        error: "Demo: preview requires a disposable QA identity.",
      })
      Object.assign(run.report, {
        summary:
          "Demo: authentication is blocked. No private preview was contacted.",
        ai_review_status: "not_run",
        scenarios: [],
      })
    }
    runs.set(pr.number, [run])
  }
}
reset()
function invalidate(number) {
  for (const stream of streams)
    stream.write(
      `event: invalidated\ndata: ${JSON.stringify({ topics: [`pull-requests/${demoRepo}/${number}`] })}\n\n`
    )
}
function schedule(callback, delay) {
  const timer = setTimeout(() => {
    jobs.delete(timer)
    callback()
  }, delay)
  jobs.add(timer)
}
const json = (res, body, status = 200) => {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  })
  res.end(JSON.stringify(body))
}
const types = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".mjs": "application/javascript",
  ".css": "text/css",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".ico": "image/x-icon",
}
function file(res, path) {
  res.writeHead(200, {
    "Content-Type": types[extname(path)] ?? "application/octet-stream",
    "Cache-Control": "no-store",
  })
  createReadStream(path).pipe(res)
}
function events(req, res, invalidations = false) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    Connection: "keep-alive",
  })
  res.write(
    invalidations
      ? 'event: hello\ndata: {"denied":[],"invalidated":[],"backend_commit":null}\n\n'
      : "event: synchronized\ndata: {}\n\n"
  )
  if (invalidations) streams.add(res)
  const keepAlive = setInterval(() => res.write(": alive\n\n"), 15_000)
  res.on("close", () => {
    streams.delete(res)
    clearInterval(keepAlive)
  })
}
function pullRequest(pr) {
  return {
    repo: demoRepo,
    number: pr.number,
    title: pr.title,
    state: "open",
    draft: false,
    additions: 24,
    deletions: 8,
    mergeable: true,
    mergeState: "CLEAN",
    headSha: revision.head,
    headRef: "designer-demo",
    reviewers: [],
    reviewRequired: true,
    statusAvailable: true,
    createdAt: date,
    updatedAt: date,
    ci: "passing",
    failingChecks: [],
    pendingChecks: [],
    missingChecks: [],
    unresolvedThreads: 0,
  }
}
function scopedPRs(scope) {
  return demoPullRequests.filter((pr) =>
    scope === "mine"
      ? pr.author === "demo-designer"
      : pr.reviewers.includes("demo-designer") || pr.teams.length > 0
  )
}

const server = http.createServer((req, res) => {
  if (![`127.0.0.1:${port}`, `localhost:${port}`].includes(req.headers.host))
    return json(res, { detail: "Local demo only" }, 403)
  if (
    req.method !== "GET" &&
    req.headers.origin &&
    ![`http://127.0.0.1:${port}`, `http://localhost:${port}`].includes(
      req.headers.origin
    )
  )
    return json(res, { detail: "Same-origin demo requests only" }, 403)
  const url = new URL(req.url, `http://127.0.0.1:${port}`)
  const path = url.pathname
  if (path === "/__demo/reset") {
    reset()
    for (const number of runs.keys()) invalidate(number)
    res.writeHead(303, { Location: "/agents/qa" })
    res.end()
    return
  }
  if (/^\/__demo\/(production|preview)\//.test(path)) {
    const point = checkpoints.find(
      ({ id }) => id === url.searchParams.get("example")
    )
    if (!point) return json(res, { detail: "Unknown demo comparison" }, 404)
    const preview = path.startsWith("/__demo/preview/")
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
    })
    res.end(
      `<!doctype html><html><head><title>${preview ? "Preview" : "Production"} · Design QA demo</title></head><body style="margin:0;font:14px system-ui;background:#f5f8fb"><header style="padding:16px">${preview ? "Preview" : "Production"} · Fictional page · ${point.title}</header>${snapshot(point.id, preview ? "head" : "base")}</body></html>`
    )
    return
  }
  if (path === "/__demo/fixtures") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" })
    res.end(`<html><body><h1>Synthetic screenshot sources</h1><p>Development-only fixture generation. No application was captured.</p><script type="module">
      const points = ${JSON.stringify(checkpoints.map(({ id }) => id))};
      for (const id of points) for (const side of ['base','head']) {
        const image = new Image(); image.src='/__demo/source/'+id+'/'+side; await image.decode();
        const canvas = document.createElement('canvas'); canvas.width=900; canvas.height=560; canvas.dataset.fixture=id+'-'+side;
        canvas.getContext('2d').drawImage(image,0,0); document.body.append(canvas);
      }
      document.body.dataset.ready='true';
    </script></body></html>`)
    return
  }
  const source = path.match(/^\/__demo\/source\/([a-z]+)\/(base|head)$/)
  if (source && checkpoints.some(({ id }) => id === source[1])) {
    res.writeHead(200, { "Content-Type": "image/svg+xml" })
    res.end(snapshot(source[1], source[2]))
    return
  }
  if (path === `${api}/ui-invalidations`) return events(req, res, true)
  if (path === `${api}/me`)
    return json(res, {
      login: "demo-designer",
      display_name: "Designer demo",
      email: null,
      avatar_url: null,
      is_admin: false,
      slack_oauth_enabled: false,
    })
  if (path === `${api}/profile`)
    return json(res, {
      login: "demo-designer",
      slack_onboarding_dismissed: true,
    })
  if (path === `${api}/frontend-reviews/inbox`)
    return json(res, {
      items: scopedPRs(url.searchParams.get("scope"))
        .filter((pr) => runs.get(pr.number)?.length)
        .map((pr) => ({
          pr: pullRequest(pr),
          run: runs.get(pr.number)[0],
          requested_reviewers: pr.reviewers,
          requested_teams: pr.teams,
        })),
      next_page: null,
      incomplete: false,
    })
  if (path === `${api}/pull-requests` || path === `${api}/review-assignments`)
    return json(res, {
      pullRequests: scopedPRs(url.searchParams.get("scope")).map(pullRequest),
      nextPage: null,
      incomplete: false,
      updatedAt: date,
    })
  const details = path.match(
    /^\/dashboard\/api\/repos\/langchain-ai\/langchainplus\/pulls\/(\d+)$/
  )
  if (details) {
    const pr = demoPullRequests.find(
      (item) => item.number === Number(details[1])
    )
    return json(res, pr ? pullRequest(pr) : null)
  }
  const match = path.match(
    /^\/dashboard\/api\/reviews\/langchain-ai\/langchainplus\/(\d+)(?:\/(.*))?$/
  )
  if (match) {
    const number = Number(match[1]),
      suffix = match[2] ?? ""
    const pr = demoPullRequests.find((item) => item.number === number)
    if (!pr)
      return json(
        res,
        {
          detail:
            "This local demo includes PRs 43396–43401 only. Try 43399 to start a scripted run.",
        },
        404
      )
    if (suffix === "frontend-reviews") {
      if (req.method === "POST") {
        req.resume()
        const run = makeRun(
          number,
          metrics,
          randomUUID(),
          `http://127.0.0.1:${port}`
        )
        Object.assign(run, {
          status: "pending",
          report: null,
          created_at: new Date().toISOString(),
        })
        runs.set(number, [run, ...runs.get(number)])
        schedule(() => {
          run.status = "running"
          invalidate(number)
        }, 800)
        schedule(() => {
          run.status = "complete"
          run.report = makeRun(
            number,
            metrics,
            run.id,
            `http://127.0.0.1:${port}`
          ).report
          run.report.ai_review_status = "running"
          invalidate(number)
        }, 2200)
        schedule(() => {
          run.report.ai_review_status = "complete"
          invalidate(number)
        }, 3600)
        return json(res, run, 201)
      }
      return json(res, { runs: runs.get(number) })
    }
    const media = suffix.match(
      /^frontend-reviews\/[^/]+\/media\/([a-z]+)-(base|head)\.png$/
    )
    if (media && checkpoints.some(({ id }) => id === media[1])) {
      const image = resolve(assets, `${media[1]}-${media[2]}.png`)
      return file(res, image)
    }
    if (suffix.startsWith("frontend-reviews/"))
      return json(
        res,
        runs.get(number).find(({ id }) => id === suffix.split("/")[1]) ?? {
          detail: "Unknown demo run",
        }
      )
    if (suffix === "")
      return json(res, {
        thread_id: null,
        status: "none",
        head_sha: revision.head,
        head_ref: "designer-demo",
        base_ref: "main",
        counts: { critical: 0, high: 0, medium: 0, low: 0, bug: 0, flag: 0 },
        updated_at: date,
        watch: false,
        pr: {
          state: "open",
          title: `Demo · ${pr.title}`,
          body: "Synthetic designer demo. No real GitHub PR was reviewed.",
          additions: 24,
          deletions: 8,
          changed_files: 3,
          commits: 1,
          head_sha: revision.head,
          head_ref: "designer-demo",
          base_ref: "main",
          author: { login: pr.author, avatar_url: null },
          created_at: date,
          merged_at: null,
          assignees: [],
          requested_reviewers: pr.reviewers.map((login) => ({
            login,
            avatar_url: null,
          })),
          labels: [],
        },
        checks: [],
        findings: [],
        walkthrough: null,
        walkthrough_running: false,
        walkthrough_error: null,
        walkthrough_scout_thread_id: null,
        walkthrough_progress: null,
        review_error: null,
      })
    if (suffix === "diff")
      return json(res, {
        files: [],
        total_additions: 24,
        total_deletions: 8,
        truncated: false,
      })
    if (suffix === "conversation") return json(res, { items: [], threads: [] })
    if (suffix === "chat")
      return json(res, {
        available: true,
        assistant_id: "agent",
        thread_id: ids.thread,
      })
    return json(res, null)
  }
  const thread = `${api}/threads/${ids.thread}`
  if (path === `${api}/workflow-approval/${ids.thread}`)
    return json(res, { approvals: [] })
  if (path === thread)
    return json(res, {
      id: ids.thread,
      title: "Demo chat · Attachment preparation only",
      repo: "frontend-review",
      repoFullName: demoRepo,
      branch: "designer-demo",
      model: "",
      status: "idle",
      viewed: true,
      ownerLogin: "demo-designer",
      transcript: "v2",
      messages: [],
      pullRequests: [],
      createdAt: Date.parse(date),
      updatedAt: Date.parse(date),
    })
  if (path === `${thread}/transcript`)
    return json(res, {
      thread_id: ids.thread,
      version: 0,
      thread: { status: "idle" },
      turns: [],
      messages: [],
      tool_calls: [],
      notices: [],
      older_cursor: null,
    })
  if (path === `${thread}/transcript/events`) return events(req, res)
  if (path === `${thread}/commands`)
    return json(
      res,
      {
        detail:
          "Demo chat prepares screenshots and a draft only. No model is connected; nothing was sent.",
      },
      503
    )
  if (path === `${thread}/background-work`)
    return json(res, { commands: [], monitors: [] })
  if (path === `${thread}/pull-request-status`)
    return json(res, { pullRequests: [] })
  if (path === `${api}/options`)
    return json(res, {
      models: [],
      default_model: "",
      default_effort: "",
      workspaces: [],
    })
  if (path === `${api}/repos`)
    return json(res, { installations: [], repositories: [] })
  if (path === `${api}/workspaces/options`) return json(res, { workspaces: [] })
  if (path.endsWith("/threads/page"))
    return json(res, { items: [], hasMore: false, nextOffset: null })
  if (path.startsWith(api)) return json(res, [])
  let candidate
  try {
    candidate = resolve(root, `.${decodeURIComponent(path)}`)
  } catch {
    return json(res, { detail: "Invalid path" }, 400)
  }
  if (
    candidate.startsWith(root + sep) &&
    existsSync(candidate) &&
    statSync(candidate).isFile()
  )
    return file(res, candidate)
  const shell = readFileSync(resolve(root, "_shell.html"), "utf8")
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
  })
  res.end(shell)
})
server.on("error", (error) => {
  console.error(error.message)
  process.exitCode = 1
})
server.listen(port, "127.0.0.1", () =>
  console.log(
    `Designer demo: http://127.0.0.1:${port}/agents/qa\nNo login, model, database, or browser-provider service is used. Reset demo restores the starting state.`
  )
)
