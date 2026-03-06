import { sanitizeBranchName } from "../git.js";

describe("sanitizeBranchName", () => {
  it("should return a valid branch name unchanged", () => {
    expect(sanitizeBranchName("feature/my-branch")).toBe("feature/my-branch");
  });

  it("should replace spaces with hyphens", () => {
    expect(sanitizeBranchName("my new branch")).toBe("my-new-branch");
  });

  it("should replace invalid characters with hyphens", () => {
    expect(sanitizeBranchName("feat~branch^name")).toBe("feat-branch-name");
    expect(sanitizeBranchName("branch:name")).toBe("branch-name");
    expect(sanitizeBranchName("branch?name*value")).toBe("branch-name-value");
    expect(sanitizeBranchName("branch[0]")).toBe("branch-0");
    expect(sanitizeBranchName("branch\\name")).toBe("branch-name");
  });

  it("should replace consecutive dots with a hyphen", () => {
    expect(sanitizeBranchName("branch..name")).toBe("branch-name");
  });

  it("should collapse consecutive slashes", () => {
    expect(sanitizeBranchName("feature//branch")).toBe("feature/branch");
  });

  it("should handle .lock in component names", () => {
    expect(sanitizeBranchName("my.lock/branch")).toBe("my-lock/branch");
    expect(sanitizeBranchName("branch.lock")).toBe("branch-lock");
  });

  it("should strip leading dots, hyphens, and slashes", () => {
    expect(sanitizeBranchName(".branch")).toBe("branch");
    expect(sanitizeBranchName("-branch")).toBe("branch");
    expect(sanitizeBranchName("/branch")).toBe("branch");
  });

  it("should strip trailing dots, hyphens, and slashes", () => {
    expect(sanitizeBranchName("branch.")).toBe("branch");
    expect(sanitizeBranchName("branch-")).toBe("branch");
    expect(sanitizeBranchName("branch/")).toBe("branch");
  });

  it("should collapse consecutive hyphens", () => {
    expect(sanitizeBranchName("branch---name")).toBe("branch-name");
  });

  it("should return fallback for empty or whitespace-only input", () => {
    expect(sanitizeBranchName("")).toBe("branch");
    expect(sanitizeBranchName("   ")).toBe("branch");
  });

  it("should handle a complex real-world example", () => {
    expect(sanitizeBranchName("  fix: resolve auth bug [OPE-13]  ")).toBe(
      "fix-resolve-auth-bug-OPE-13",
    );
  });
});
