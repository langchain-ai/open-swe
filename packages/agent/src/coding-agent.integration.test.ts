import { spawnSync } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { fakeModel } from "@langchain/core/testing"
import { FakeStreamingChatModel } from "@langchain/core/utils/testing"
import { afterEach, describe, expect, it } from "vitest"

import { createLocalWorkspace } from "@open-swe/workspace"

import { createCodingAgentGraph } from "./coding-agent.js"

const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true })
})

function runGit(cwd: string, ...args: string[]): string {
  const result = spawnSync("git", args, { cwd, encoding: "utf8" })
  if (result.status !== 0) throw new Error(result.stderr || `git ${args.join(" ")} failed`)
  return result.stdout
}

async function collect<T>(source: AsyncIterable<T>): Promise<T[]> {
  const values: T[] = []
  for await (const value of source) values.push(value)
  return values
}

describe("coding agent", () => {
  it("inspects, edits, executes, and streams against a real repository", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-coding-agent-"))
    roots.push(root)
    const project = path.join(root, "project")
    const allowlist = path.join(root, "projects.json")
    const artifacts = path.join(root, "artifacts")
    fs.mkdirSync(project)
    fs.writeFileSync(path.join(project, "message.txt"), "before\n")
    fs.writeFileSync(
      path.join(project, "verify.mjs"),
      'import fs from "node:fs"\nif (fs.readFileSync("message.txt", "utf8") !== "after\\n") process.exit(1)\n'
    )
    fs.writeFileSync(allowlist, JSON.stringify([project]))
    runGit(project, "init", "-q")
    runGit(project, "config", "user.name", "Test")
    runGit(project, "config", "user.email", "test@example.com")
    runGit(project, "add", ".")
    runGit(project, "commit", "-qm", "fixture")

    const backend = await createLocalWorkspace(
      { localProjectPath: project, threadId: "coding-agent-test" },
      {
        OPEN_SWE_LOCAL_PROJECTS_FILE: allowlist,
        OPEN_SWE_LOCAL_ARTIFACTS_DIR: artifacts,
        PATH: process.env.PATH,
      }
    )
    const model = fakeModel()
      .respondWithTools([
        { name: "read_file", id: "read", args: { file_path: "/message.txt" } },
      ])
      .respondWithTools([
        {
          name: "write_file",
          id: "write",
          args: { file_path: "/message.txt", content: "after\n" },
        },
      ])
      .respondWithTools([
        {
          name: "execute",
          id: "execute",
          args: { command: `${JSON.stringify(process.execPath)} verify.mjs` },
        },
      ])
      .respond(new AIMessage("Updated the file and verified the result."))
    const agent = createCodingAgentGraph({ model, backend })
    const stream = await agent.streamEvents(
      { messages: [new HumanMessage("Update the message and verify it.")] },
      { version: "v3", configurable: { thread_id: "coding-agent-test" } }
    )

    const [toolCalls, messages, output] = await Promise.all([
      collect(stream.toolCalls),
      collect(stream.messages),
      stream.output,
    ])

    expect(toolCalls.map((call) => call.name)).toEqual([
      "read_file",
      "write_file",
      "execute",
    ])
    expect(messages.length).toBeGreaterThan(0)
    expect(output.messages.at(-1)?.text).toContain("verified")
    expect(fs.readFileSync(path.join(project, "message.txt"), "utf8")).toBe("after\n")
    expect(runGit(project, "diff", "--", "message.txt")).toContain("+after")
  })

  it("stops a streaming turn when its abort signal is cancelled", async () => {
    const controller = new AbortController()
    const model = new FakeStreamingChatModel({
      responses: [new AIMessage("This response should not finish streaming.")],
      sleep: 30,
    })
    const agent = createCodingAgentGraph({ model })
    const stream = await agent.streamEvents(
      { messages: [new HumanMessage("Keep going until cancelled.")] },
      {
        version: "v3",
        configurable: { thread_id: "coding-agent-cancel-test" },
        signal: controller.signal,
      }
    )

    const startedAt = Date.now()
    setTimeout(() => controller.abort(), 60)
    await expect(stream.output).rejects.toMatchObject({ name: "AbortError" })
    expect(Date.now() - startedAt).toBeLessThan(1_000)
  })
})
