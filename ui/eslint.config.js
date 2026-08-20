//  @ts-check

import { tanstackConfig } from "@tanstack/eslint-config"

export default [
  {
    ignores: [
      ".output/**",
      ".nitro/**",
      ".tanstack/**",
      "dev-dist/**",
      "dist/**",
      "src/routeTree.gen.ts",
      "src/components/ui/**",
      "src/features/agents/experiments/**",
    ],
  },
  ...tanstackConfig,
]
