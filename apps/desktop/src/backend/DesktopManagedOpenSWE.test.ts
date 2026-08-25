import { assert, describe, it } from "@effect/vitest";

import { makeManagedOpenSWECommand } from "./DesktopManagedOpenSWE.ts";

describe("DesktopManagedOpenSWE", () => {
  it("builds an argv-only authenticated LangGraph command", () => {
    const command = makeManagedOpenSWECommand({
      pythonPath: "/repo/.venv/bin/python",
      configPath: "/repo/langgraph.desktop.json",
      cwd: "/repo",
      port: 2025,
      token: "secret-token",
      projectsFile: "/state/open-swe/projects.json",
      artifactsDir: "/state/open-swe/artifacts",
      inheritedEnv: { PATH: "/usr/bin" },
    });

    assert.equal(command._tag, "StandardCommand");
    if (command._tag !== "StandardCommand") return;
    assert.equal(command.command, "/repo/.venv/bin/python");
    assert.deepEqual(command.args, [
      "-c",
      "from langgraph_cli.cli import cli; cli()",
      "dev",
      "--no-reload",
      "--no-browser",
      "--host",
      "127.0.0.1",
      "--port",
      "2025",
      "--config",
      "/repo/langgraph.desktop.json",
    ]);
    assert.equal(command.options.cwd, "/repo");
    assert.equal(command.options.env?.PYTHONDONTWRITEBYTECODE, "1");
    assert.equal(command.options.env?.OPEN_SWE_LOCAL_AUTH_TOKEN, "secret-token");
    assert.equal(
      command.options.env?.OPEN_SWE_LOCAL_PROJECTS_FILE,
      "/state/open-swe/projects.json",
    );
    assert.equal(command.options.env?.OPEN_SWE_LOCAL_ARTIFACTS_DIR, "/state/open-swe/artifacts");
    assert.equal(command.options.extendEnv, false);
  });
});
