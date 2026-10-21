import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { viteSingleFile } from "vite-plugin-singlefile"

// The dashboard loads a plugin as one self-contained HTML file with no network,
// so every script and style is inlined into bundle/index.html.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: { outDir: "bundle", emptyOutDir: true },
})
