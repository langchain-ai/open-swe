import { describe, expect, it } from "vitest";

import { normalizeRepoFullName } from "./repo";

describe("normalizeRepoFullName", () => {
  it("accepts owner/repo", () => {
    expect(normalizeRepoFullName("langchain-ai/langgraph")).toBe("langchain-ai/langgraph");
  });

  it("accepts github URLs", () => {
    expect(normalizeRepoFullName("https://github.com/withmartian/code-review-benchmark")).toBe(
      "withmartian/code-review-benchmark",
    );
  });

  it("rejects invalid input", () => {
    expect(normalizeRepoFullName("not-a-repo")).toBeNull();
  });
});
