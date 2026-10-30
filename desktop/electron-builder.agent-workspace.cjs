const { readFile } = require("node:fs/promises");
const path = require("node:path");
const { build } = require("./package.json");
const { version } = require("./agent-workspace.json");

module.exports = {
  ...build,
  appId: "com.langchain.agentworkspace",
  productName: "Agent Workspace",
  extraMetadata: { name: "agent-workspace-desktop", version },
  directories: { output: "dist-agent-workspace" },
  publish: {
    provider: "generic",
    url: "https://github.com/langchain-ai/open-swe/releases/download/agent-workspace-updates",
    channel: "latest",
  },
  extraResources: [
    {
      from: "resources/agent-workspace-app-update.yml",
      to: "app-update.yml",
    },
    { from: "resources/icon.png", to: "icon.png" },
  ],
  dmg: { ...build.dmg, sign: true, writeUpdateInfo: false },
  mac: {
    ...build.mac,
    artifactName: "Agent-Workspace-${version}-${arch}-mac.${ext}",
    category: "public.app-category.productivity",
    entitlements: "entitlements.agent-workspace.mac.plist",
  },
  beforePack: async () => {
    const marker = await readFile(
      path.join(__dirname, "build", "app-variant.cjs"),
      "utf8",
    );
    if (marker.trim() !== 'module.exports = "agent-workspace";') {
      throw new Error("Build the Agent Workspace variant before packaging it");
    }
  },
};
