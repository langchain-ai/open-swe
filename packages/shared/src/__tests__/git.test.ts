import { sanitizeBranchName } from "../git.js";

describe("sanitizeBranchName", () => {
  it("should return a simple valid name unchanged", () => {
    expect(sanitizeBranchName("feature/my-branch")).toBe("feature/my-branch");
  });

  it("should replace spaces with hyphens", () => {
    expect(sanitizeBranchName("my new branch")).toBe("my-new-branch");
  });

  it("should replace invalid characters with hyphens", () => {
    expect(sanitizeBranchName("feat~branch^name")).toBe("feat-branch-name");
  });

  it("should collapse consecutive dots into a hyphen", () => {
    expect(sanitizeBranchName("feat..branch")).toBe("feat-branch");
  });

  it("should strip leading dots, hyphens, and slashes", () => {
    expect(sanitizeBranchName("..feature")).toBe("feature");
    expect(sanitizeBranchName("-feature")).toBe("feature");
    expect(sanitizeBranchName("/feature")).toBe("feature");
  });

  it("should strip trailing dots, hyphens, and slashes", () => {
    expect(sanitizeBranchName("feature.")).toBe("feature");
    expect(sanitizeBranchName("feature-")).toBe("feature");
    expect(sanitizeBranchName("feature/")).toBe("feature");
  });

  it("should replace .lock at path boundaries", () => {
    expect(sanitizeBranchName("my.lock/branch")).toBe("my-lock/branch");
    expect(sanitizeBranchName("branch.lock")).toBe("branch-lock");
  });

  it("should replace @{ sequence", () => {
    expect(sanitizeBranchName("branch@{0}")).toBe("branch-{0}");
  });

  it("should collapse multiple hyphens into one", () => {
    expect(sanitizeBranchName("a~~b")).toBe("a-b");
  });

  it("should return fallback for empty or all-invalid input", () => {
    expect(sanitizeBranchName("")).toBe("branch");
    expect(sanitizeBranchName("...")).toBe("branch");
  });
});
