import { describe, expect, it } from "vitest"

import {
  pickComposerRepo,
  pickComposerWorkspace,
  reposForWorkspace,
  workspaceForRepository,
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
  { full_name: "acme/oss" },
  { full_name: "acme/tools" },
  { full_name: "Acme/Internal" },
  { full_name: "acme/shared" },
]
const names = (repos: Array<{ full_name: string }>) =>
  repos.map((repo) => repo.full_name)

describe("reposForWorkspace", () => {
  it("offers app-wide workspaces all accessible repos without adding inaccessible ones", () => {
    expect(
      reposForWorkspace(
        "oss",
        workspaces.map((w) => ({ ...w, all_repositories: w.slug === "oss" })),
        accessible
      )
    ).toEqual(accessible)
  })

  it("offers a workspace the accessible repositories it owns plus its default", () => {
    expect(names(reposForWorkspace("oss", workspaces, accessible))).toEqual([
      "acme/oss",
    ])
    // An unassigned default inherited from the instance is still usable.
    expect(names(reposForWorkspace("docs", workspaces, accessible))).toEqual([
      "acme/shared",
    ])
  })

  it("offers the default workspace everything no other workspace claims", () => {
    expect(names(reposForWorkspace("default", workspaces, accessible))).toEqual(
      ["acme/tools", "Acme/Internal", "acme/shared"]
    )
    expect(names(reposForWorkspace(null, [], accessible))).toEqual(
      names(accessible)
    )
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

describe("workspaceForRepository", () => {
  it("routes shared repositories independently of API order and preserves explicit picks", () => {
    const shared = [
      { slug: "zebra", repos: ["acme/api"], is_default: false },
      { slug: "alpha", repos: ["Acme/API"], is_default: false },
      { slug: "default", repos: [], is_default: true },
    ]
    expect(workspaceForRepository("ACME/api", shared)).toBe("alpha")
    expect(workspaceForRepository("acme/api", [...shared].reverse())).toBe(
      "alpha"
    )
    expect(workspaceForRepository("acme/unbound", shared)).toBeNull()
    const withDefault = shared.map((w) => ({ ...w, repos: ["acme/api"] }))
    const repoWorkspace = workspaceForRepository("acme/api", withDefault)
    expect(repoWorkspace).toBe("default")
    expect(
      pickComposerWorkspace({
        ...base,
        workspaces: shared,
        repoWorkspace,
        override: "zebra",
      })
    ).toBe("zebra")
  })
})
