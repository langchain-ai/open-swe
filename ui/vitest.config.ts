import { defineConfig } from "vitest/config"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"

// Tests get their own config rather than reusing vite.config.ts, which adds
// nitro. Nitro's Vite environments leave Vitest resolving React through the
// server module runner, where React's CJS entry throws `module is not defined`
// and the per-file jsdom environment never applies. TanStack Start stays: it
// rewrites server-function calls to client fetches, and without it modules like
// the agents api client resolve to their server variant and throw looking for
// a StartEvent.
export default defineConfig({
  plugins: [tanstackStart(), viteReact({ compiler: true })],
  resolve: { tsconfigPaths: true },
  test: {
    // Per-file `@vitest-environment jsdom` docblocks pick the environment;
    // node stays the default so the tests that read files off disk keep a
    // `file:` `import.meta.url`.
    // jsdom refuses `localStorage` on an opaque origin, which is what an
    // unset url gives you.
    environmentOptions: { jsdom: { url: "http://localhost:3000" } },
    setupFiles: ["./vitest.setup.ts"],
  },
})
