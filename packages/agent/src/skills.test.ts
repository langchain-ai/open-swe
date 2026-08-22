import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

import { skillSources } from "./skills.js"

function temporaryDirectory(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-skills-"))
}

describe("skillSources", () => {
  it("orders home before project and Claude last, skipping absent directories", () => {
    const home = temporaryDirectory()
    const project = temporaryDirectory()
    for (const directory of [
      path.join(home, ".agents", "skills"),
      path.join(home, ".claude", "skills"),
      path.join(project, ".agents", "skills"),
      path.join(project, ".claude", "skills"),
    ]) {
      fs.mkdirSync(directory, { recursive: true })
    }

    expect(skillSources(project, home)).toEqual([
      path.join(home, ".agents", "skills"),
      path.join(project, ".agents", "skills"),
      path.join(home, ".claude", "skills"),
      path.join(project, ".claude", "skills"),
    ])
  })

  it("returns nothing when no skill directory exists", () => {
    expect(skillSources(temporaryDirectory(), temporaryDirectory())).toEqual([])
  })

  it("omits project sources when no project is selected", () => {
    const home = temporaryDirectory()
    fs.mkdirSync(path.join(home, ".claude", "skills"), { recursive: true })

    expect(skillSources(null, home)).toEqual([
      path.join(home, ".claude", "skills"),
    ])
  })
})
