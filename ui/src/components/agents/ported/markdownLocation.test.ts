import { describe, expect, it } from "vitest";

import { parseLocationHref } from "./markdownLocation";

describe("parseLocationHref", () => {
  it("parses a path with a start-end range", () => {
    expect(parseLocationHref("#loc=src/foo.rs:231-232")).toEqual({
      file: "src/foo.rs",
      startLine: 231,
      endLine: 232,
    });
  });

  it("parses a single-line ref (no end)", () => {
    expect(parseLocationHref("#loc=mod.rs:42")).toEqual({
      file: "mod.rs",
      startLine: 42,
      endLine: 42,
    });
  });

  it("tolerates an origin prefix the URL transform may prepend", () => {
    expect(parseLocationHref("http://localhost/#loc=a/b.ts:5-9")).toEqual({
      file: "a/b.ts",
      startLine: 5,
      endLine: 9,
    });
  });

  it("decodes percent-encoded hrefs", () => {
    expect(parseLocationHref("#loc=a%20b/c.ts:1-2")).toEqual({
      file: "a b/c.ts",
      startLine: 1,
      endLine: 2,
    });
  });

  it("clamps an end below the start to a single line", () => {
    expect(parseLocationHref("#loc=x.ts:10-3")).toEqual({
      file: "x.ts",
      startLine: 10,
      endLine: 10,
    });
  });

  it("returns null for non-location and malformed hrefs", () => {
    expect(parseLocationHref(undefined)).toBeNull();
    expect(parseLocationHref("https://example.com")).toBeNull();
    expect(parseLocationHref("#loc=noline")).toBeNull();
    expect(parseLocationHref("#loc=:5")).toBeNull();
    expect(parseLocationHref("#loc=x.ts:abc")).toBeNull();
  });
});
