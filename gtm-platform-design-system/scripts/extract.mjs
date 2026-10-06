/*
 * Extracts the design system out of the GTM app into this package.
 *
 * The app is the source today, so this runs repeatably and `--verify` fails CI
 * on drift. A one-shot manual copy would fork on day one; this cannot.
 *
 * It resolves the dependency closure rather than trusting a hand-written file
 * list: start from the two component directories, follow every import, and pull
 * in what they reach. Three things stop the walk. A product edge (a route, the
 * agent session) excludes the importing file with a reason. A shim replaces an
 * app-only leaf with this package's own standalone version. A framework import
 * is rewritten onto the host adapter. Anything else that fails to resolve is a
 * hard error, because the alternative is publishing a module that cannot build.
 */
import {
  readFileSync,
  writeFileSync,
  readdirSync,
  mkdirSync,
  rmSync,
  existsSync,
} from "node:fs";
import { dirname, join, basename } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = dirname(here);
const web = dirname(dirname(pkg));
const src = join(web, "src");
const VERIFY = process.argv.includes("--verify");

/* Reaches the product: a route, product state, or generated API types. */
const EXCLUDE = {
  "patterns/app-rail-chrome.tsx": "imports @/app/whats-new and the product nav",
  "patterns/approval-artifact.tsx": "imports @/app/inbox/conversation-card",
  "patterns/command-palette.tsx": "imports @/app/accounts data modules",
  "patterns/posture-bar.tsx": "imports @/app/nav-routes",
  "patterns/agent-companion-chrome.tsx": "needs agent-session state injected",
  "patterns/agent-dock-slot.tsx": "needs agent-session state injected",
  "patterns/agent-layout-menu.tsx": "needs agent-session state injected",
  "patterns/personal-appearance.tsx": "binds generated /v1 schema types",
  "patterns/onboarding-previews.tsx": "GTM onboarding copy and imagery",
  "patterns/surface-placeholder.tsx": "GTM placeholder copy",
  "patterns/site-hop.tsx": "GTM site switcher; its SiteMark ships as patterns/site-mark",
  "ui/logo.tsx": "LangChain brand mark",
  "ui/mark-tile-icon.ts": "LangChain favicon machinery, app chrome",
  "ui/theme-toggle.tsx": "drives the app favicon; the host adapter owns theme",
  "ui/bird-sprite.tsx": "GTM mascot",
  "ui/bird-preview.tsx": "GTM mascot",
};

/*
 * App-only leaves this package answers itself. The left side is the app's
 * `@/lib` path; the right side is a module written by hand in src/lib.
 */
const SHIMS = {
  "api/client": "problem",
  "api/problem-message": "problem",
  "api/schema": "schema-lite",
};

/* Component-level shims: an app module this package re-answers itself. */
const COMPONENT_SHIMS = {
  "patterns/site-hop": "patterns/site-mark",
};

/*
 * The product data layer. A component that reaches any of these is a product
 * surface wearing a pattern's name, so the walk stops and blames the importer
 * rather than dragging TanStack hooks and GTM brand state into the package.
 */
const FORBID = [
  /^query\/(?!search-debounce)/,
  /^agent-stream/,
  /^agent-session/,
  /^agent-activity/,
  /^agent-turn-presentation/,
  /^site-mark/,
  /^gtm-bird/,
  /^bird-/,
  /^draft-batch/,
  /^crm-filter-fields/,
  /^product$/,
  /^api$/,
];

const isTest = (f) => /\.test\.(ts|tsx)$/.test(f);
const flat = (p) => p.replace(/\//g, "__");
const ROOTS = ["ui", "patterns", "data-grid"];

const report = {
  components: {},
  lib: [],
  excluded: [],
  hostPorts: [],
  shimmed: [],
  errors: [],
};

function appPath(rel) {
  for (const ext of [".tsx", ".ts"]) {
    const p = join(src, rel + ext);
    if (existsSync(p)) return p;
  }
  return null;
}

/* Rewrites one file's imports and records every edge it could not keep. */
function rewrite(code, kind, file) {
  const label = `${kind}/${file}`;
  let out = code;
  const reach = [];

  const target = (dir, name) => (kind === dir ? `./${name}` : `../${dir}/${name}`);

  for (const dir of ROOTS) {
    out = out.replace(
      new RegExp(`@/components/${dir}/([a-z0-9-]+)`, "g"),
      (_m, n) => {
        const shim = COMPONENT_SHIMS[`${dir}/${n}`];
        if (shim !== undefined) {
          const [sd, sn] = shim.split("/");
          report.shimmed.push(`${label}: ${dir}/${n} -> ${shim}`);
          return target(sd, sn);
        }
        reach.push(`${dir}/${n}`);
        return target(dir, n);
      },
    );
  }

  out = out.replace(/@\/lib\/([a-z0-9/-]+)/g, (_m, n) => {
    const shim = SHIMS[n];
    if (shim !== undefined) {
      report.shimmed.push(`${label}: @/lib/${n} -> lib/${shim}`);
      return `../lib/${shim}`;
    }
    if (FORBID.some((rule) => rule.test(n))) {
      report.errors.push(`${label}: reaches the product data layer (@/lib/${n})`);
      return `../lib/${flat(n)}`;
    }
    reach.push(`lib:${n}`);
    return `../lib/${flat(n)}`;
  });

  out = out.replace(/@\/hooks\/([a-z0-9-]+)/g, (_m, n) => {
    reach.push(`hooks:${n}`);
    return `../lib/hooks__${n}`;
  });

  for (const m of out.matchAll(/from "(@\/[a-z0-9/-]+)"/g)) {
    report.errors.push(`${label}: unhandled app import ${m[1]}`);
  }

  for (const m of out.matchAll(/from "(next\/[a-z-]+|next-themes)"/g)) {
    report.hostPorts.push(`${label}: ${m[1]}`);
  }
  out = out
    .replace(/^import Link from "next\/link";$/m, 'import { HostLink as Link } from "../host";')
    .replace(/^import Image from "next\/image";$/m, 'import { HostImage as Image } from "../host";')
    .replace(/^import \{ useTheme \} from "next-themes";$/m, 'import { useTheme } from "../host";')
    .replace(
      /^import \{ LangSmithMark \} from "\.\.?\/?(?:ui\/)?logo";$/m,
      'import { HostBrandMark as LangSmithMark } from "../host";',
    );

  for (const m of out.matchAll(/from "(next\/[a-z-]+|next-themes)"/g)) {
    report.errors.push(`${label}: unported framework import ${m[1]}`);
  }
  return { code: out, reach };
}

const written = new Map();
const seen = new Set();

function take(kind, file) {
  const key = `${kind}/${file.replace(/\.(tsx|ts)$/, "")}`;
  if (seen.has(key)) return;
  seen.add(key);

  const name = file.replace(/\.(tsx|ts)$/, "");
  const reason = EXCLUDE[`${kind}/${name}.tsx`] ?? EXCLUDE[`${kind}/${name}.ts`];
  if (reason !== undefined) {
    report.excluded.push({ file: `${kind}/${name}`, reason });
    return;
  }

  const from = appPath(join("components", kind, name));
  if (from === null) {
    report.errors.push(`${kind}/${name}: not found in app`);
    return;
  }
  const ext = from.endsWith(".tsx") ? ".tsx" : ".ts";
  const { code, reach } = rewrite(readFileSync(from, "utf8"), kind, name + ext);
  written.set(join("src", kind, name + ext), code);
  (report.components[kind] ??= []).push(name);

  for (const edge of reach) {
    if (edge.startsWith("lib:")) takeLib(edge.slice(4));
    else if (edge.startsWith("hooks:")) takeLib(edge.slice(6), "hooks");
    else {
      const [d, n] = edge.split("/");
      take(d, n);
    }
  }
}

function takeLib(rel, base = "lib") {
  const key = `${base}:${rel}`;
  if (seen.has(key)) return;
  seen.add(key);

  const from = appPath(join(base, rel));
  if (from === null) {
    report.errors.push(`${base}/${rel}: not found in app`);
    return;
  }
  const ext = from.endsWith(".tsx") ? ".tsx" : ".ts";
  const name = base === "hooks" ? `hooks__${flat(rel)}` : flat(rel);
  const { code, reach } = rewrite(readFileSync(from, "utf8"), "lib", rel + ext);
  written.set(join("src", "lib", name + ext), code);
  report.lib.push(name);

  for (const edge of reach) {
    if (edge.startsWith("lib:")) takeLib(edge.slice(4));
    else if (edge.startsWith("hooks:")) takeLib(edge.slice(6), "hooks");
    else {
      const [d, n] = edge.split("/");
      take(d, n);
    }
  }
}

for (const kind of ROOTS) {
  for (const file of readdirSync(join(src, "components", kind)).sort()) {
    if (!/\.(tsx|ts)$/.test(file) || isTest(file)) continue;
    take(kind, file);
  }
}

/* lib/ is written last so a hand-authored shim is never clobbered. */
const HAND_WRITTEN = new Set([
  "src/lib/problem.ts",
  "src/lib/schema-lite.ts",
  "src/host.tsx",
  "src/patterns/site-mark.tsx",
]);

if (!VERIFY) {
  for (const kind of [...ROOTS, "lib"]) {
    const dir = join(pkg, "src", kind);
    for (const f of existsSync(dir) ? readdirSync(dir) : []) {
      if (HAND_WRITTEN.has(`src/${kind}/${f}`)) continue;
      rmSync(join(dir, f), { force: true });
    }
    mkdirSync(dir, { recursive: true });
  }
}
for (const [rel, code] of written) {
  if (HAND_WRITTEN.has(rel)) continue;
  const target = join(pkg, rel);
  if (VERIFY) {
    const have = existsSync(target) ? readFileSync(target, "utf8") : null;
    if (have !== code) report.errors.push(`DRIFT ${rel}`);
  } else {
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, code);
  }
}

/*
 * The exclusion list is published rather than kept in this script, because the
 * first question a consumer asks about a 115-component package is about the
 * component that is not in it.
 */
const excludedDoc = [
  "# Not in the package",
  "",
  "The GTM Platform internal design system, created by Amal Irgashev.",
  "",
  "Generated by scripts/extract.mjs. Each entry stayed in the application for",
  "the stated reason, which is always one of two: it reaches a product route or",
  "product state, or it is a brand asset belonging to the application rather",
  "than to the system.",
  "",
  "| Component | Why it stayed |",
  "|---|---|",
  ...report.excluded
    .slice()
    .sort((a, b) => a.file.localeCompare(b.file))
    .map((e) => `| \`${e.file}\` | ${e.reason} |`),
  "",
  "## Ported through the host adapter",
  "",
  "These shipped, with their framework import replaced by a host slot:",
  "",
  ...report.hostPorts.map((p) => `- ${p}`),
  "",
  "## Answered by this package instead",
  "",
  "An application-only module replaced by a standalone version here:",
  "",
  ...report.shimmed.map((p) => `- ${p}`),
  "",
].join("\n");

{
  const target = join(pkg, "agent", "EXCLUDED.md");
  if (VERIFY) {
    const have = existsSync(target) ? readFileSync(target, "utf8") : null;
    if (have !== excludedDoc) report.errors.push("DRIFT agent/EXCLUDED.md");
  } else {
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, excludedDoc);
  }
}

const summary = {
  ...Object.fromEntries(
    Object.entries(report.components).map(([k, v]) => [k, v.length]),
  ),
  lib: report.lib.length,
  excluded: report.excluded.length,
  hostPorts: report.hostPorts.length,
  shimmed: report.shimmed.length,
  errors: report.errors.length,
};
console.log(JSON.stringify({ summary, ...report }, null, 2));
if (report.errors.length > 0) process.exit(1);
