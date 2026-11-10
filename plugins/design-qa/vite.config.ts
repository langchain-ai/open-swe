import { readFileSync } from "node:fs"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, type Plugin } from "vite"
import { viteSingleFile } from "vite-plugin-singlefile"

// An app is one HTML file the web UI runs with no network: every script,
// style, font and image is inlined, and manifest.json goes in as a script the
// backend reads when the file is published, with review.md as the
// instructions of its review step.
function manifest(): Plugin {
  return {
    name: "openswe-manifest",
    transformIndexHtml: () => {
      const declared = JSON.parse(readFileSync("manifest.json", "utf8"))
      const review = {
        ...declared.review,
        instructions: readFileSync("review.md", "utf8").trim(),
      }
      return [
        {
          tag: "script",
          attrs: { type: "application/openswe-plugin+json" },
          children: JSON.stringify({ ...declared, review }).replaceAll(
            "<",
            "\\u003c"
          ),
          injectTo: "head-prepend",
        },
      ]
    },
  }
}

export default defineConfig({
  plugins: [tailwindcss(), react(), viteSingleFile(), manifest()],
  build: { outDir: "bundle", assetsInlineLimit: Number.MAX_SAFE_INTEGER },
})
