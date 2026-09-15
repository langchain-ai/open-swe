import { defineConfig } from "vitest/config"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [tanstackStart(), viteReact({ compiler: true })],
  resolve: { tsconfigPaths: true },
  test: {
    include: ["src/**/*.gap.test.ts", "src/**/*.gap.test.tsx"],
    environmentOptions: { jsdom: { url: "http://localhost:3000" } },
    setupFiles: ["./vitest.setup.ts"],
  },
})
