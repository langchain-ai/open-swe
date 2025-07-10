import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["**/*.eval.?(c|m)[jt]s"],
    reporters: ["langsmith/vitest/reporter"],
    setupFiles: ["dotenv/config"],
    typecheck: {
      tsconfig: "./eval.tsconfig.json",
    },
    testTimeout: 1800_000, // 30 minutes
  },
});
