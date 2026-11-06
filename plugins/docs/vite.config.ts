import tailwindcss from "@tailwindcss/vite"
import { tanstackStart } from "@tanstack/react-start/plugin/vite"
import viteReact from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// A plugin ships as one self-contained HTML file that the dashboard runs with
// no network: Start builds the SPA shell, every route in one chunk, and
// scripts/inline-bundle.mjs folds its scripts and styles into bundle/index.html.
export default defineConfig({
  resolve: { tsconfigPaths: true },
  plugins: [
    tailwindcss(),
    tanstackStart({ spa: { enabled: true } }),
    viteReact(),
  ],
  environments: {
    client: {
      build: { rolldownOptions: { output: { inlineDynamicImports: true } } },
    },
  },
})
