//  @ts-check

import { tanstackConfig } from "@tanstack/eslint-config"
import pluginQuery from "@tanstack/eslint-plugin-query"
import pluginRouter from "@tanstack/eslint-plugin-router"
import reactHooks from "eslint-plugin-react-hooks"

export default [
  {
    ignores: [
      ".output/**",
      ".nitro/**",
      ".tanstack/**",
      "dev-dist/**",
      "dist/**",
      "public/**",
      "src/routeTree.gen.ts",
      "src/components/ui/**",
      "src/features/agents/experiments/**",
    ],
  },
  ...tanstackConfig,
  reactHooks.configs.flat["recommended-latest"],
  ...pluginQuery.configs["flat/recommended"],
  ...pluginRouter.configs["flat/recommended"],
]
