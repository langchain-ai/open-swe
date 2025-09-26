import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Hono } from "hono";
import { registerRunRoute, resolveInsideRoot } from "../run.js";

describe("resolveInsideRoot", () => {
  const originalRoot = process.env.WORKSPACES_ROOT;

  afterEach(() => {
    if (originalRoot === undefined) {
      delete process.env.WORKSPACES_ROOT;
    } else {
      process.env.WORKSPACES_ROOT = originalRoot;
    }
  });

  it("returns the resolved path when inside the workspaces root", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-root-"));
    const workspace = fs.mkdtempSync(path.join(root, "repo-"));
    process.env.WORKSPACES_ROOT = root;

    const resolved = resolveInsideRoot(workspace);

    expect(resolved).toBe(fs.realpathSync(workspace));
  });

  it("throws when WORKSPACES_ROOT is missing", () => {
    delete process.env.WORKSPACES_ROOT;

    expect(() => resolveInsideRoot("/tmp"))
      .toThrowErrorMatchingInlineSnapshot(
        `"WORKSPACES_ROOT environment variable is not set."`,
      );
  });

  it("throws when workspaceAbsPath is blank", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-root-"));
    process.env.WORKSPACES_ROOT = root;

    expect(() => resolveInsideRoot(" "))
      .toThrowErrorMatchingInlineSnapshot(`"workspaceAbsPath is required."`);
  });

  it("throws when the resolved path escapes the root", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-root-"));
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-outside-"));
    process.env.WORKSPACES_ROOT = root;

    expect(() => resolveInsideRoot(outside)).toThrow(
      `Resolved workspace path "${fs.realpathSync(outside)}" is outside of the configured root "${fs.realpathSync(root)}".`,
    );
  });

  it("rejects symlinks that resolve outside of the root", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-root-"));
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-outside-"));
    const symlink = path.join(root, "linked-workspace");
    process.env.WORKSPACES_ROOT = root;

    try {
      fs.symlinkSync(outside, symlink, "dir");
    } catch (error) {
      if (
        process.platform === "win32" &&
        (error as NodeJS.ErrnoException)?.code === "EPERM"
      ) {
        return;
      }
      throw error;
    }

    expect(() => resolveInsideRoot(symlink)).toThrow(
      `Resolved workspace path "${fs.realpathSync(outside)}" is outside of the configured root "${fs.realpathSync(root)}".`,
    );
  });
});

describe("registerRunRoute", () => {
  const originalRoot = process.env.WORKSPACES_ROOT;

  afterEach(() => {
    if (originalRoot === undefined) {
      delete process.env.WORKSPACES_ROOT;
    } else {
      process.env.WORKSPACES_ROOT = originalRoot;
    }
  });

  it("accepts requests with a workspace inside the root", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-root-"));
    const workspace = fs.mkdtempSync(path.join(root, "repo-"));
    process.env.WORKSPACES_ROOT = root;

    const app = new Hono();
    registerRunRoute(app);

    const response = await app.request("/run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ workspaceAbsPath: workspace }),
    });

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toEqual({
      resolvedWorkspaceAbsPath: fs.realpathSync(workspace),
    });
  });

  it("rejects requests when WORKSPACES_ROOT is missing", async () => {
    delete process.env.WORKSPACES_ROOT;

    const app = new Hono();
    registerRunRoute(app);

    const response = await app.request("/run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ workspaceAbsPath: "/tmp" }),
    });

    expect(response.status).toBe(500);
    const body = await response.json();
    expect(body.error).toBe("WORKSPACES_ROOT environment variable is not set.");
  });

  it("rejects requests when the workspace path escapes the root", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-root-"));
    const outside = fs.mkdtempSync(path.join(os.tmpdir(), "open-swe-outside-"));
    process.env.WORKSPACES_ROOT = root;

    const app = new Hono();
    registerRunRoute(app);

    const response = await app.request("/run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ workspaceAbsPath: outside }),
    });

    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.error).toBe(
      `Resolved workspace path "${fs.realpathSync(outside)}" is outside of the configured root "${fs.realpathSync(root)}".`,
    );
  });
});
