import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { environment: "jsdom", globals: false, include: ["probe/**/*.test.tsx"] },
  esbuild: { jsx: "automatic" },
});
