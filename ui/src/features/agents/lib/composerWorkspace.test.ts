import { describe, expect, it } from "vitest"

import { pickComposerWorkspace } from "./composerWorkspace"

const base = {
  override: null,
  repoWorkspace: null,
  userDefault: null,
  instanceDefault: "default",
  workspaces: [{ slug: "default" }, { slug: "oss" }],
}

describe("pickComposerWorkspace", () => {
  it("prefers the user's default over the instance default when no repository decides", () => {
    expect(pickComposerWorkspace({ ...base, userDefault: "oss" })).toBe("oss")
    expect(pickComposerWorkspace(base)).toBe("default")
  })

  it("lets the repository's owner outrank the user's default", () => {
    expect(
      pickComposerWorkspace({
        ...base,
        repoWorkspace: "default",
        userDefault: "oss",
      })
    ).toBe("default")
  })

  it("keeps an explicit pick", () => {
    expect(
      pickComposerWorkspace({
        ...base,
        override: "oss",
        repoWorkspace: "default",
      })
    ).toBe("oss")
  })

  it("ignores a default that names no listed workspace", () => {
    expect(pickComposerWorkspace({ ...base, userDefault: "gone" })).toBe(
      "default"
    )
    expect(
      pickComposerWorkspace({
        ...base,
        userDefault: "gone",
        workspaces: [{ slug: "oss" }],
      })
    ).toBeNull()
  })
})
