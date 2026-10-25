import { createHash } from "node:crypto"
import { readdir, readFile, writeFile } from "node:fs/promises"
import { resolve } from "node:path"

const root = resolve(import.meta.dirname, "../.output/public")
const files = []
for (const entry of await readdir(root, {
  recursive: true,
  withFileTypes: true,
})) {
  if (!entry.isFile() || /\.(?:gz|br|zst|map)$/.test(entry.name)) continue
  const path = resolve(entry.parentPath, entry.name).slice(root.length)
  if (path === "/desktop-ui-manifest.json") continue
  const body = await readFile(`${root}${path}`)
  files.push({ path, sha256: createHash("sha256").update(body).digest("hex") })
}
await writeFile(`${root}/desktop-ui-manifest.json`, JSON.stringify({ files }))
