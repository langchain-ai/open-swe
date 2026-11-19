import { describe, expect, it } from "vitest"

import { pickComposerRepo } from "./composerRepo"

describe("pickComposerRepo", () => {
  const offered = [{ full_name: "acme/oss" }, { full_name: "acme/docs" }]

  it("keeps an explicit pick, including an explicit none", () => {
    const defaults = {
      userDefault: "acme/oss",
      defaultRepo: "acme/oss",
    }
    expect(
      pickComposerRepo({ ...defaults, override: "acme/docs", offered })
    ).toBe("acme/docs")
    expect(
      pickComposerRepo({ ...defaults, override: null, offered })
    ).toBeNull()
  })

  it("prefers the user's default when offered, else Robin's, else none", () => {
    expect(
      pickComposerRepo({
        override: undefined,
        userDefault: "ACME/docs",
        defaultRepo: "acme/oss",
        offered,
      })
    ).toBe("acme/docs")
    expect(
      pickComposerRepo({
        override: undefined,
        userDefault: "acme/tools",
        defaultRepo: "acme/oss",
        offered,
      })
    ).toBe("acme/oss")
    expect(
      pickComposerRepo({
        override: undefined,
        userDefault: "acme/tools",
        defaultRepo: null,
        offered,
      })
    ).toBeNull()
  })
})
