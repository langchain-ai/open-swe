import { describe, expect, it } from "@jest/globals";
import { dedupeBindsPreferRW } from "@openswe/sandbox-docker";

describe("dedupeBindsPreferRW", () => {
  it("prefers rw entries when the same container target is mounted multiple times", () => {
    const binds = [
      "/host/a:/workspace/src:ro",
      "/host/b:/workspace/src:rw",
    ];

    expect(dedupeBindsPreferRW(binds)).toEqual(["/host/b:/workspace/src:rw"]);
  });

  it("preserves order while normalizing container paths when replacing ro with rw", () => {
    const binds = [
      "/host/a:/workspace/src/:ro",
      "/host/b:/workspace/data:ro",
      "/host/c:/workspace/src:rw",
    ];

    expect(dedupeBindsPreferRW(binds)).toEqual([
      "/host/c:/workspace/src:rw",
      "/host/b:/workspace/data:ro",
    ]);
  });

  it("treats binds without an explicit mode as rw and keeps them over ro duplicates", () => {
    const binds = [
      "/host/a:/workspace/cache",
      "/host/b:/workspace/cache:ro",
    ];

    expect(dedupeBindsPreferRW(binds)).toEqual(["/host/a:/workspace/cache"]);
  });
});
