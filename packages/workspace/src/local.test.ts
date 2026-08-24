import fs from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import {
  createLocalWorkspace,
  createLocalWorkspaceBackend,
  resolveLocalProject,
  sanitizeShellEnvironment,
} from "./local.js"

const roots: string[] = []

function temporaryDirectory(): string {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "open-swe-workspace-")
  )
  roots.push(directory)
  return directory
}

afterEach(() => {
  for (const root of roots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

describe("resolveLocalProject", () => {
  it("accepts only the canonical allowlisted project root", () => {
    const root = temporaryDirectory()
    const project = path.join(root, "project")
    const child = path.join(project, "child")
    fs.mkdirSync(child, { recursive: true })
    const allowlist = path.join(root, "projects.json")
    fs.writeFileSync(allowlist, JSON.stringify([{ cwd: project }]))

    const environment = { OPEN_SWE_LOCAL_PROJECTS_FILE: allowlist }
    expect(
      resolveLocalProject({ localProjectPath: project }, environment)
    ).toBe(fs.realpathSync(project))
    expect(() =>
      resolveLocalProject({ localProjectPath: child }, environment)
    ).toThrow("not an allowed project directory")
  })

  it("does not authorize stale allowlist entries", () => {
    const root = temporaryDirectory()
    const missing = path.join(root, "missing")
    const allowlist = path.join(root, "projects.json")
    fs.writeFileSync(allowlist, JSON.stringify([missing]))

    expect(() =>
      resolveLocalProject(
        { localProjectPath: missing },
        { OPEN_SWE_LOCAL_PROJECTS_FILE: allowlist }
      )
    ).toThrow("not an allowed project directory")
  })
})

describe("sanitizeShellEnvironment", () => {
  it("retains shell essentials without leaking credentials", () => {
    expect(
      sanitizeShellEnvironment({
        HOME: "/home/person",
        PATH: "/usr/bin",
        OPENAI_API_KEY: "test-key",
        GH_TOKEN: "test-token",
      })
    ).toEqual({ HOME: "/home/person", PATH: "/usr/bin" })
  })
})

describe("createLocalWorkspace", () => {
  it("resolves the allowlisted project from each run configuration", async () => {
    const root = temporaryDirectory()
    const project = path.join(root, "project")
    fs.mkdirSync(project)
    fs.writeFileSync(path.join(project, "marker.txt"), "selected\n")
    const allowlist = path.join(root, "projects.json")
    fs.writeFileSync(allowlist, JSON.stringify([project]))

    const factory = createLocalWorkspaceBackend({
      OPEN_SWE_LOCAL_PROJECTS_FILE: allowlist,
      OPEN_SWE_LOCAL_ARTIFACTS_DIR: path.join(root, "artifacts"),
      PATH: process.env.PATH,
    })
    const backend = await factory({
      configurable: {
        local_project_path: project,
        thread_id: "configured-thread",
      },
    } as never)

    expect(await backend.read(path.join(project, "marker.txt"))).toMatchObject({
      content: "selected\n",
    })
    expect(await backend.read("marker.txt")).toMatchObject({
      content: "selected\n",
    })
  })

  it("reads skill directories outside the selected project", async () => {
    const root = temporaryDirectory()
    const project = path.join(root, "project")
    const outside = path.join(root, "skills", "demo")
    fs.mkdirSync(project)
    fs.mkdirSync(outside, { recursive: true })
    fs.writeFileSync(path.join(outside, "SKILL.md"), "instructions\n")
    const allowlist = path.join(root, "projects.json")
    fs.writeFileSync(allowlist, JSON.stringify([project]))

    const backend = await createLocalWorkspace(
      { localProjectPath: project, threadId: "thread" },
      {
        OPEN_SWE_LOCAL_PROJECTS_FILE: allowlist,
        OPEN_SWE_LOCAL_ARTIFACTS_DIR: path.join(root, "artifacts"),
        PATH: process.env.PATH,
      }
    )

    expect(await backend.read(path.join(outside, "SKILL.md"))).toMatchObject({
      content: "instructions\n",
    })
  })

  it("executes in the project with a sanitized environment and external artifacts", async () => {
    const root = temporaryDirectory()
    const project = path.join(root, "project")
    const artifacts = path.join(root, "artifacts")
    fs.mkdirSync(project)
    const allowlist = path.join(root, "projects.json")
    fs.writeFileSync(allowlist, JSON.stringify([project]))

    const backend = await createLocalWorkspace(
      { localProjectPath: project, threadId: "../thread" },
      {
        OPEN_SWE_LOCAL_PROJECTS_FILE: allowlist,
        OPEN_SWE_LOCAL_ARTIFACTS_DIR: artifacts,
        PATH: process.env.PATH,
        SECRET_VALUE: "must-not-leak",
      }
    )
    const result = await backend.execute(
      `${JSON.stringify(process.execPath)} -p "process.cwd() + '|' + (process.env.SECRET_VALUE || '')"`
    )

    expect(result.exitCode).toBe(0)
    expect(result.output.trim()).toBe(`${fs.realpathSync(project)}|`)
    const [artifactThread] = fs.readdirSync(artifacts)
    expect(artifactThread).not.toContain("..")
    expect(fs.realpathSync(path.join(artifacts, artifactThread!))).not.toBe(
      fs.realpathSync(project)
    )
  })
})
