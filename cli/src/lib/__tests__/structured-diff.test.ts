import { mkdtempSync, rmSync, writeFileSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";
import {
  buildToolPatch,
  createStructuredPatchHunks,
  diffLinesToStructuredHunk,
} from "../structured-diff.js";

describe("structured diff", () => {
  it("creates hunks from edit contents", () => {
    const hunks = createStructuredPatchHunks({
      oldContent: "const value = 1;",
      newContent: "const value = 2;",
      lineStart: 12,
    });

    expect(hunks).toEqual([
      {
        oldStart: 12,
        oldLines: 1,
        newStart: 12,
        newLines: 1,
        lines: ["-const value = 1;", "+const value = 2;"],
      },
    ]);
  });

  it("uses legacy diff line numbers when converting old diffLines output", () => {
    const hunks = diffLinesToStructuredHunk([
      { type: "context", oldLine: 9, newLine: 9, text: "before" },
      { type: "remove", oldLine: 10, text: "old" },
      { type: "add", newLine: 10, text: "new" },
    ]);

    expect(hunks[0]?.oldStart).toBe(9);
    expect(hunks[0]?.newStart).toBe(9);
    expect(hunks[0]?.lines).toEqual([" before", "-old", "+new"]);
  });

  it("finds the edited line in the current file for edit_file tool calls", () => {
    const dir = mkdtempSync(join(tmpdir(), "coda-structured-diff-"));
    const filePath = join(dir, "sample.ts");
    writeFileSync(filePath, ["one", "const value = 2;", "three"].join("\n"));

    try {
      const patch = buildToolPatch("edit_file", {
        file_path: filePath,
        old_string: "const value = 1;",
        new_string: "const value = 2;",
      });

      expect(patch?.hunks[0]?.oldStart).toBe(2);
      expect(patch?.hunks[0]?.lines).toEqual([
        "-const value = 1;",
        "+const value = 2;",
      ]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
