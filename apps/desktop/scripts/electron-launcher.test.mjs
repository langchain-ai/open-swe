import { assert, describe, it } from "vite-plus/test";

import {
  makeDevelopmentLauncherScript,
  resolveElectronBinaryPath,
  resolveMacLauncherIconPaths,
  resolveMacLauncherPaths,
} from "./electron-launcher.mjs";

describe("electron development launcher", () => {
  it("uses captured values only as fallbacks for a live runner environment", () => {
    const script = makeDevelopmentLauncherScript({
      electronBinaryPath: "/repo/node_modules/electron/Electron",
      mainEntryPath: "/repo/apps/desktop/dist-electron/main.cjs",
      desktopRoot: "/repo/apps/desktop",
      environment: {
        VITE_DEV_SERVER_URL: "http://127.0.0.1:8526",
        OPENSWE_PORT: "16566",
        OPENSWE_HOME: "/tmp/openswe",
      },
    });

    assert.include(
      script,
      "if [ -z \"${VITE_DEV_SERVER_URL:-}\" ]; then export VITE_DEV_SERVER_URL='http://127.0.0.1:8526'; fi",
    );
    assert.notInclude(script, "\nexport VITE_DEV_SERVER_URL=");
    assert.include(
      script,
      "exec '/repo/node_modules/electron/Electron' --open-swe-dev-root='/repo/apps/desktop' '/repo/apps/desktop/dist-electron/main.cjs' \"$@\"",
    );
  });

  it("repairs Electron before loading the package entrypoint", () => {
    const calls = [];
    const electronPath = resolveElectronBinaryPath({
      ensureRuntime: () => {
        calls.push("ensure");
      },
      createRequire: () => (specifier) => {
        calls.push(`require:${specifier}`);
        return "/repo/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron";
      },
      moduleUrl: import.meta.url,
    });

    assert.equal(
      electronPath,
      "/repo/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
    );
    assert.deepEqual(calls, ["ensure", "require:electron"]);
  });

  it("keeps the native Electron executable name inside the branded macOS bundle", () => {
    const paths = resolveMacLauncherPaths(
      "/repo/apps/desktop/.electron-runtime/Open SWE (Dev).app",
      "Open SWE (Dev)",
    );

    assert.equal(paths.launcherExecutableName, "Open SWE (Dev) Launcher");
    assert.equal(
      paths.launcherBinaryPath,
      "/repo/apps/desktop/.electron-runtime/Open SWE (Dev).app/Contents/MacOS/Open SWE (Dev) Launcher",
    );
    assert.equal(
      paths.runtimeElectronBinaryPath,
      "/repo/apps/desktop/.electron-runtime/Open SWE (Dev).app/Contents/MacOS/Electron",
    );

    const script = makeDevelopmentLauncherScript({
      electronBinaryPath: paths.runtimeElectronBinaryPath,
      mainEntryPath: "/repo/apps/desktop/dist-electron/main.cjs",
      desktopRoot: "/repo/apps/desktop",
      environment: {},
    });
    assert.include(
      script,
      "exec '/repo/apps/desktop/.electron-runtime/Open SWE (Dev).app/Contents/MacOS/Electron'",
    );
    assert.notInclude(script, "node_modules/electron");
  });

  it("derives launcher icons from canonical development and production assets", () => {
    const development = resolveMacLauncherIconPaths("/runtime", true);
    const production = resolveMacLauncherIconPaths("/runtime", false);

    assert.match(development.sourceIconPath, /assets\/dev\/blueprint-macos-1024\.png$/);
    assert.equal(development.generatedIconPath, "/runtime/icon-dev.icns");
    assert.match(production.sourceIconPath, /assets\/prod\/langchain-logo\.png$/);
    assert.equal(production.generatedIconPath, "/runtime/icon-prod.icns");
  });
});
