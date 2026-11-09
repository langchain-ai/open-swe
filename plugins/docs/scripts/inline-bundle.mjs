// Folds Start's SPA build into the one self-contained HTML file an app ships
// (bundle/index.html), with manifest.json inside it. The dashboard runs it with
// no network, so every script, stylesheet and font must be inside it. Fonts
// keep their Latin subsets only.
import { readFileSync, readdirSync, mkdirSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const root = join(dirname(fileURLToPath(import.meta.url)), "..")
const client = join(root, "dist", "client")
const asset = (href) => join(client, href.replace(/^\//, ""))

let html = readFileSync(join(client, "_shell.html"), "utf8")

const chunks = readdirSync(join(client, "assets")).filter((f) =>
  f.endsWith(".js")
)
if (chunks.length !== 1)
  throw new Error(`expected one client chunk, found ${chunks.join(", ")}`)

function inlineFonts(css) {
  return css.replace(/@font-face\s*{[^}]*}/g, (rule) => {
    const woff2 = /url\(([^)]*-latin-\d+-normal-[^)]*\.woff2)\)/.exec(rule)
    if (!woff2) return ""
    const data = readFileSync(asset(woff2[1].replace(/^["']|["']$/g, "")))
    const src = `url(data:font/woff2;base64,${data.toString("base64")}) format("woff2")`
    return rule.replace(/src:[^;}]*/, `src:${src}`)
  })
}

html = html.replace(
  /<link rel="stylesheet" href="([^"]+)"[^>]*\/?>/g,
  (_, href) =>
    `<style>${inlineFonts(readFileSync(asset(href), "utf8"))}</style>`
)
html = html.replace(/<link rel="modulepreload" href="[^"]+"\/?>/g, "")
const manifest = readFileSync(join(root, "manifest.json"), "utf8")
html = html.replace(
  "</head>",
  `<script type="application/openswe-plugin+json">${JSON.stringify(
    JSON.parse(manifest)
  ).replaceAll("<", "\\u003c")}</script></head>`
)
html = html.replace(
  /<script type="module" async="" src="([^"]+)"><\/script>/g,
  (_, src) =>
    `<script type="module">${readFileSync(asset(src), "utf8").replaceAll(
      "</script",
      "<\\/script"
    )}</script>`
)
// Raw NULs sit only inside script string literals (Start's own hydration data
// has one), where the escape means the same; the HTML parser would turn a raw
// one into U+FFFD, and PostgreSQL text refuses them.
html = html.replaceAll("\0", "\\u0000")
if (/\s(?:src|href)="\/assets\//.test(html))
  throw new Error("bundle/index.html still references /assets/")

mkdirSync(join(root, "bundle"), { recursive: true })
writeFileSync(join(root, "bundle", "index.html"), html)
console.log(`bundle/index.html: ${(html.length / 1024).toFixed(0)} kB`)
