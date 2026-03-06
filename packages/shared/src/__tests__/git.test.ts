import { sanitizeBranchName } from "../git.js";

describe("sanitizeBranchName", () => {
  it("should return a valid branch name unchanged", () => {
    expect(sanitizeBranchName("feature/my-branch")).toBe("feature/my-branch");
  });

  it("should replace spaces with hyphens", () => {
    expect(sanitizeBranchName("my new branch")).toBe("my-new-branch");
  });

  it("should replace invalid characters with hyphens", () => {
    expect(sanitizeBranchName("feat~branch^1:test")).toBe("feat-branch-1-test");
    expect(sanitizeBranchName("branch?name*here")).toBe("branch-name-here");
    expect(sanitizeBranchName("branch[0]")).toBe("branch-0");
  });

  it("should replace consecutive dots with a hyphen", () => {
    expect(sanitizeBranchName("branch..name")).toBe("branch-name");
  });

  it("should replace @{ sequence", () => {
    expect(sanitizeBranchName("branch@{0}")).toBe("branch-0");
  });

  it("should strip leading dots and slashes", () => {
    expect(sanitizeBranchName(".branch")).toBe("branch");
    expect(sanitizeBranchName("/branch")).toBe("branch");
  });

  it("should strip trailing dots and slashes", () => {
    expect(sanitizeBranchName("branch.")).toBe("branch");
    expect(sanitizeBranchName("branch/")).toBe("branch");
  });

  it("should handle .lock in path components", () => {
    expect(sanitizeBranchName("my.lock/branch")).toBe("my-lock/branch");
    expect(sanitizeBranchName("branch.lock")).toBe("branch-lock");
  });

  it("should collapse multiple consecutive hyphens", () => {
    expect(sanitizeBranchName("branch~~name")).toBe("branch-name");
  });

  it("should return fallback for empty or all-invalid input", () => {
    expect(sanitizeBranchName("")).toBe("branch");
    expect(sanitizeBranchName("...")).toBe("branch");
  });

  it("should handle backslashes", () => {
    expect(sanitizeBranchName("feature\\branch")).toBe("feature-branch");
  });
});
