import { EnvironmentId, ProjectId } from "@openswe/contracts";
import { describe, expect, it } from "vite-plus/test";

import type { EnvironmentProject } from "./models.ts";
import {
  buildProjectGroups,
  derivePhysicalProjectKey,
  type ProjectGroupingSettings,
} from "./projectGrouping.ts";

const environmentId = EnvironmentId.make("environment");
const repositoryIdentity = {
  canonicalKey: "github.com/openswe/openswe",
  locator: {
    source: "git-remote" as const,
    remoteName: "upstream",
    remoteUrl: "https://github.com/openswe/openswe.git",
  },
  provider: "github",
  owner: "openswe",
  name: "openswe",
  displayName: "Open SWE",
};

function makeProject(
  id: string,
  workspaceRoot: string,
  overrides: Partial<EnvironmentProject> = {},
): EnvironmentProject {
  return {
    environmentId,
    id: ProjectId.make(id),
    title: id,
    workspaceRoot,
    repositoryIdentity,
    defaultModelSelection: null,
    scripts: [],
    createdAt: "2026-07-01T00:00:00.000Z",
    updatedAt: "2026-07-01T00:00:00.000Z",
    ...overrides,
  };
}

function settings(
  mode: ProjectGroupingSettings["sidebarProjectGroupingMode"],
  overrides: ProjectGroupingSettings["sidebarProjectGroupingOverrides"] = {},
): ProjectGroupingSettings {
  return {
    sidebarProjectGroupingMode: mode,
    sidebarProjectGroupingOverrides: overrides,
  };
}

describe("buildProjectGroups", () => {
  it("preserves every physical clone as a selectable member in repository modes", () => {
    const projects = [
      makeProject("openswe", "/work/openswe"),
      makeProject("openswe-2", "/work/openswe-2"),
      makeProject("openswe-3", "/work/openswe-3"),
    ];

    for (const mode of ["repository", "repository_path"] as const) {
      const groups = buildProjectGroups({ projects, settings: settings(mode) });
      expect(groups).toHaveLength(1);
      expect(groups[0]?.members.map((member) => member.project.id)).toEqual([
        "openswe",
        "openswe-2",
        "openswe-3",
      ]);
      expect(groups[0]?.memberProjectRefs).toHaveLength(3);
    }
  });

  it("uses a shared custom title as the repository group's label", () => {
    const projects = [
      makeProject("first", "/work/openswe", { title: "Custom project" }),
      makeProject("second", "/work/openswe-2", { title: "Custom project" }),
    ];

    expect(buildProjectGroups({ projects, settings: settings("repository") })[0]?.label).toBe(
      "Custom project",
    );
  });

  it("keeps the repository label when shared titles match its repository name", () => {
    const projects = [
      makeProject("first", "/work/openswe", { title: "openswe" }),
      makeProject("second", "/work/openswe-2", { title: "openswe" }),
    ];

    expect(buildProjectGroups({ projects, settings: settings("repository") })[0]?.label).toBe(
      "Open SWE",
    );
  });

  it("keeps physical clones in separate groups when requested", () => {
    const projects = [
      makeProject("openswe", "/work/openswe"),
      makeProject("openswe-2", "/work/openswe-2"),
      makeProject("openswe-3", "/work/openswe-3"),
    ];

    const groups = buildProjectGroups({ projects, settings: settings("separate") });
    expect(groups).toHaveLength(3);
    expect(groups.flatMap((group) => group.members)).toHaveLength(3);
    expect(groups.map((group) => group.label)).toEqual(["openswe", "openswe-2", "openswe-3"]);
  });

  it("applies a physical-project override without dropping its siblings", () => {
    const first = makeProject("openswe", "/work/openswe");
    const second = makeProject("openswe-2", "/work/openswe-2");
    const third = makeProject("openswe-3", "/work/openswe-3");
    const groups = buildProjectGroups({
      projects: [first, second, third],
      settings: settings("repository", {
        [derivePhysicalProjectKey(second)]: "separate",
      }),
    });

    expect(groups).toHaveLength(2);
    expect(groups.flatMap((group) => group.members.map((member) => member.project.id))).toEqual([
      "openswe",
      "openswe-3",
      "openswe-2",
    ]);
  });

  it("dedupes stale registrations at one physical path using the freshest project", () => {
    const stale = makeProject("stale", "/work/openswe", {
      repositoryIdentity: null,
      updatedAt: "2026-07-01T00:00:00.000Z",
    });
    const fresh = makeProject("fresh", "/work/openswe/", {
      updatedAt: "2026-07-02T00:00:00.000Z",
    });

    const groups = buildProjectGroups({
      projects: [stale, fresh],
      settings: settings("repository"),
    });
    expect(groups).toHaveLength(1);
    expect(groups[0]?.members).toHaveLength(1);
    expect(groups[0]?.representative.id).toBe("fresh");
    expect(groups[0]?.memberProjectRefs).toHaveLength(2);
  });

  it("uses repository identity from a duplicate registration when the winner lacks it", () => {
    const identified = makeProject("identified", "/work/openswe", {
      updatedAt: "2026-07-01T00:00:00.000Z",
    });
    const freshUnidentified = makeProject("fresh", "/work/openswe/", {
      repositoryIdentity: null,
      updatedAt: "2026-07-02T00:00:00.000Z",
    });
    const sibling = makeProject("sibling", "/work/openswe-2");

    const groups = buildProjectGroups({
      projects: [identified, freshUnidentified, sibling],
      settings: settings("repository"),
    });
    expect(groups).toHaveLength(1);
    expect(groups[0]?.members.map((member) => member.project.id)).toEqual(["fresh", "sibling"]);
  });

  it("uses the freshest winner's repository identity when stale duplicates disagree", () => {
    const staleIdentity = {
      ...repositoryIdentity,
      canonicalKey: "github.com/openswe/old-repository",
      name: "old-repository",
      displayName: "Old Repository",
    };
    const stale = makeProject("stale", "/work/openswe", {
      repositoryIdentity: staleIdentity,
      updatedAt: "2026-07-01T00:00:00.000Z",
    });
    const fresh = makeProject("fresh", "/work/openswe/", {
      updatedAt: "2026-07-02T00:00:00.000Z",
    });
    const sibling = makeProject("sibling", "/work/openswe-2");

    const groups = buildProjectGroups({
      projects: [stale, fresh, sibling],
      settings: settings("repository"),
    });
    expect(groups).toHaveLength(1);
    expect(groups[0]?.members.map((member) => member.project.id)).toEqual(["fresh", "sibling"]);
  });

  it("uses the freshest identity-bearing duplicate when the winner lacks identity", () => {
    const staleIdentity = {
      ...repositoryIdentity,
      canonicalKey: "github.com/openswe/old-repository",
      name: "old-repository",
      displayName: "Old Repository",
    };
    const staleIdentified = makeProject("stale-identified", "/work/openswe", {
      repositoryIdentity: staleIdentity,
      updatedAt: "2026-07-01T00:00:00.000Z",
    });
    const freshIdentified = makeProject("fresh-identified", "/work/openswe/", {
      updatedAt: "2026-07-02T00:00:00.000Z",
    });
    const winner = makeProject("winner", "/work/openswe", {
      repositoryIdentity: null,
      updatedAt: "2026-07-03T00:00:00.000Z",
    });
    const sibling = makeProject("sibling", "/work/openswe-2");

    const groups = buildProjectGroups({
      projects: [staleIdentified, freshIdentified, winner, sibling],
      settings: settings("repository"),
    });
    expect(groups).toHaveLength(1);
    expect(groups[0]?.members.map((member) => member.project.id)).toEqual(["winner", "sibling"]);
  });
});
