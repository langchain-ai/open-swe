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
    ],
  },
  ...tanstackConfig,
]
