//  @ts-check

import { tanstackConfig } from "@tanstack/eslint-config"

export default [
  {
    ignores: [
      ".output/**",
      ".nitro/**",
      ".tanstack/**",
      "dist/**",
      "src/routeTree.gen.ts",
      "src/components/ui/**",
      // Vendored AI Elements (shadcn registry) components — kept close to
      // upstream so they can be re-pulled; not linted to our local style.
      "src/components/ai-elements/**",
      // Ported from a desktop host app and excluded from tsconfig (not yet
      // integrated), so type-aware linting cannot resolve them. Keep this list
      // in sync with the `exclude` entries in tsconfig.json.
      "src/components/agents/ported/ChatView.tsx",
      "src/components/agents/ported/PromptBar.tsx",
      "src/components/agents/ported/BranchSelector.tsx",
      "src/components/agents/ported/ContextIndicator.tsx",
      "src/components/agents/ported/SourceControlPanel.tsx",
      "src/components/agents/ported/SourceControlTile.tsx",
      "src/components/agents/ported/Footer.tsx",
      "src/components/agents/ported/ThreadPicker.tsx",
    ],
  },
  ...tanstackConfig,
]
