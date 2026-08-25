import { assert, describe, it } from "vite-plus/test";

import {
  hasSpecificPierreIconForFileName,
  resolvePierreIconForEntry,
  syntheticFileNameForLanguageId,
  OPEN_SWE_PIERRE_ICONS,
} from "./pierre-icons";

describe("Pierre file icons", () => {
  it("uses Pierre exact filename and complete-set extension mappings", () => {
    assert.equal(resolvePierreIconForEntry("Dockerfile", "file")?.token, "docker");
    assert.equal(resolvePierreIconForEntry("src/Button.tsx", "file")?.token, "react");
    assert.equal(resolvePierreIconForEntry("vite.config.ts", "file")?.token, "vite");
  });

  it("extends Pierre with OpenSWE-specific exact filename icons", () => {
    assert.equal(
      resolvePierreIconForEntry("package.json", "file")?.name,
      "openswe-file-icon-package-json",
    );
    assert.equal(
      resolvePierreIconForEntry("config/tsconfig.json", "file")?.name,
      "openswe-file-icon-tsconfig",
    );
    assert.equal(resolvePierreIconForEntry("AGENTS.md", "file")?.name, "openswe-file-icon-agents");
    assert.equal(resolvePierreIconForEntry("CLAUDE.md", "file")?.name, "openswe-file-icon-claude");
    assert.equal(resolvePierreIconForEntry("README.md", "file")?.name, "openswe-file-icon-readme");
    assert.equal(
      resolvePierreIconForEntry("pnpm-lock.yaml", "file")?.name,
      "openswe-file-icon-pnpm",
    );
    assert.equal(
      resolvePierreIconForEntry("pnpm-workspace.yaml", "file")?.name,
      "openswe-file-icon-pnpm",
    );
  });

  it("ships every custom icon referenced by the extended resolver", () => {
    const customIconNames = new Set(Object.values(OPEN_SWE_PIERRE_ICONS.byFileName));
    for (const iconName of customIconNames) {
      assert.include(OPEN_SWE_PIERRE_ICONS.spriteSheet, `id="${iconName}"`);
    }
  });

  it("uses the Pierre default icon for unknown file types", () => {
    assert.equal(resolvePierreIconForEntry("artifact.unknown-ext", "file")?.token, "default");
    assert.isFalse(hasSpecificPierreIconForFileName("artifact.unknown-ext"));
  });

  it("leaves directory rendering to the shared folder fallback", () => {
    assert.isNull(resolvePierreIconForEntry("packages/client-runtime", "directory"));
  });

  it("normalizes common markdown fence language aliases", () => {
    assert.equal(syntheticFileNameForLanguageId("typescript"), "file.ts");
    assert.equal(syntheticFileNameForLanguageId("shellscript"), "file.sh");
    assert.equal(syntheticFileNameForLanguageId("python"), "file.py");
  });
});
