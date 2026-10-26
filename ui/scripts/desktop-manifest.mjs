import { createHash } from "node:crypto"
import { readdir, readFile, mkdir, writeFile } from "node:fs/promises"
import { resolve } from "node:path"
import { gzipSync } from "node:zlib"

const root = resolve(import.meta.dirname, "../.output/public")
const output = resolve(import.meta.dirname, "../.output/desktop")
const files = []
for (const entry of await readdir(root, {
  recursive: true,
  withFileTypes: true,
})) {
  if (!entry.isFile() || /\.(?:gz|br|zst|map)$/.test(entry.name)) continue
  const path = resolve(entry.parentPath, entry.name).slice(root.length)
  if (path === "/desktop-ui-manifest.json") continue
  const body = await readFile(`${root}${path}`)
  files.push({
    path,
    body: body.toString("base64"),
  })
}
const archive = gzipSync(JSON.stringify({ files }))
await mkdir(output, { recursive: true })
await writeFile(`${output}/ui.json.gz`, archive)

// Images build without a git checkout or SOURCE_COMMIT; only a release needs
// the descriptor, so a missing commit must not fail the dashboard build.
const { commit } = JSON.parse(
  await readFile(`${root}/open-swe-build-info.json`, "utf8")
)
if (/^[a-f0-9]{40}$/.test(commit ?? "")) {
  await writeFile(
    `${output}/descriptor.json`,
    JSON.stringify({
      bridge_version: 1,
      url: `https://github.com/langchain-ai/open-swe/releases/download/desktop-ui-${commit}/ui.json.gz`,
      sha256: createHash("sha256").update(archive).digest("hex"),
    })
  )
}
