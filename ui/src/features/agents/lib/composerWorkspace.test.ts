import { describe, expect, it } from "vitest"

import {
  pickComposerRepo,
  pickComposerWorkspace,
  reposForWorkspace,
} from "./composerWorkspace"

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

const workspaces = [
  { slug: "default", repos: [], is_default: true, default_repo: null },
  {
    slug: "oss",
    repos: ["acme/oss", "acme/docs"],
    is_default: false,
    default_repo: "acme/oss",
  },
  {
    slug: "docs",
    repos: ["acme/site"],
    is_default: false,
    default_repo: "acme/shared",
  },
]
const accessible = [
  { full_name: "acme/oss", private: false },
  { full_name: "acme/tools", private: true },
  { full_name: "Acme/Internal", private: true },
  { full_name: "acme/shared", private: true },
  { full_name: "acme/public", private: false },
]
const names = (repos: Array<{ full_name: string }>) =>
  repos.map((repo) => repo.full_name)

describe("reposForWorkspace", () => {
  it("offers a named workspace only the accessible repositories it owns", () => {
    expect(names(reposForWorkspace("oss", workspaces, accessible))).toEqual([
      "acme/oss",
    ])
    expect(names(reposForWorkspace("docs", workspaces, accessible))).toEqual([])
  })

  it("offers the default workspace only private repositories no other workspace claims", () => {
    expect(names(reposForWorkspace("default", workspaces, accessible))).toEqual(
      ["acme/tools", "Acme/Internal", "acme/shared"]
    )
    expect(names(reposForWorkspace(null, [], accessible))).toEqual(
      ["acme/tools", "Acme/Internal", "acme/shared"]
    )
  })

  it("offers public repositories only when explicitly assigned to default", () => {
    const defaults = [
      {
        slug: "default",
        repos: ["ACME/oss"],
        is_default: true,
        default_repo: "acme/public",
      },
    ]
    expect(names(reposForWorkspace("default", defaults, accessible))).toEqual([
      "acme/oss", "acme/tools", "Acme/Internal", "acme/shared",
    ])
  })
})

describe("pickComposerRepo", () => {
  const offered = [{ full_name: "acme/oss" }, { full_name: "acme/docs" }]

  it("keeps an explicit pick, including an explicit none", () => {
    const defaults = { userDefault: "acme/oss", workspaceDefault: "acme/oss" }
    expect(
      pickComposerRepo({ ...defaults, override: "acme/docs", offered })
    ).toBe("acme/docs")
    expect(
      pickComposerRepo({ ...defaults, override: null, offered })
    ).toBeNull()
  })

  it("prefers the user's default when offered, else the workspace's, else none", () => {
    expect(
      pickComposerRepo({
        override: undefined,
        userDefault: "ACME/docs",
        workspaceDefault: "acme/oss",
        offered,
      })
    ).toBe("acme/docs")
    expect(
      pickComposerRepo({
        override: undefined,
        userDefault: "acme/tools",
        workspaceDefault: "acme/oss",
        offered,
      })
    ).toBe("acme/oss")
    expect(
      pickComposerRepo({
        override: undefined,
        userDefault: "acme/tools",
        workspaceDefault: null,
        offered,
      })
    ).toBeNull()
  })
})
