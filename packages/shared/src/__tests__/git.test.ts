import { getRepoAbsolutePath } from "../git.js";
import { SANDBOX_ROOT_DIR } from "../constants.js";

describe("getRepoAbsolutePath", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.OPEN_SWE_LOCAL_MODE;
    delete process.env.OPEN_SWE_LOCAL_PROJECT_PATH;
    delete process.env.OPEN_SWE_PROJECT_PATH;
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  it("should return sandbox path when not in local mode", () => {
    const result = getRepoAbsolutePath({ owner: "test", repo: "my-repo" });
    expect(result).toBe(`${SANDBOX_ROOT_DIR}/my-repo`);
  });

  it("should return local working directory when local mode is set via env", () => {
    process.env.OPEN_SWE_LOCAL_MODE = "true";
    process.env.OPEN_SWE_LOCAL_PROJECT_PATH = "/tmp/test-project";
    const result = getRepoAbsolutePath({ owner: "test", repo: "my-repo" });
    expect(result).toBe("/tmp/test-project");
  });

  it("should return local working directory via env even without config", () => {
    process.env.OPEN_SWE_LOCAL_MODE = "true";
    process.env.OPEN_SWE_LOCAL_PROJECT_PATH = "/tmp/local-project";
    const result = getRepoAbsolutePath({ owner: "test", repo: "my-repo" });
    expect(result).toBe("/tmp/local-project");
  });

  it("should return local working directory when config has local mode", () => {
    process.env.OPEN_SWE_LOCAL_PROJECT_PATH = "/tmp/config-project";
    const config = {
      configurable: { "x-local-mode": "true" },
    };
    const result = getRepoAbsolutePath(
      { owner: "test", repo: "my-repo" },
      config as any,
    );
    expect(result).toBe("/tmp/config-project");
  });

  it("should throw when repo name is missing and not in local mode", () => {
    expect(() =>
      getRepoAbsolutePath({ owner: "test", repo: "" }),
    ).toThrow("No repository name provided");
  });
});
