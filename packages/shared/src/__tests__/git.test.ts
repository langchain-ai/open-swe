import { jest } from "@jest/globals";

// tests will import getRepoAbsolutePath after setting env variable

describe("getRepoAbsolutePath", () => {
  const originalSandboxRoot = process.env.SANDBOX_ROOT_DIR;

  afterEach(() => {
    jest.resetModules();
    if (originalSandboxRoot === undefined) {
      delete process.env.SANDBOX_ROOT_DIR;
    } else {
      process.env.SANDBOX_ROOT_DIR = originalSandboxRoot;
    }
  });

  it("resolves repository path using SANDBOX_ROOT_DIR env", async () => {
    process.env.SANDBOX_ROOT_DIR = "/tmp/custom-root";
    jest.resetModules();
    const { getRepoAbsolutePath } = await import("../git.js");
    const target = { owner: "foo", repo: "bar" };
    expect(getRepoAbsolutePath(target)).toBe("/tmp/custom-root/bar");
  });
});
