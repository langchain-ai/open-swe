#!/usr/bin/env node
/*
 * The package's command surface: three verbs, all read-only.
 *
 * `find` and `rules` exist because an agent that has to read a 200KB JSON index
 * to learn one component's name has already spent the context it was going to
 * write the component with. `doctor` exists because the useful half of this
 * system is unenforceable by types: a consumer can import every primitive
 * correctly and still hand-roll a confirm dialog or hardcode a hex. It is the
 * check an agent runs on its own work before claiming it is done.
 *
 * None of it is a tool in the MCP sense, deliberately. These are commands an
 * agent in any harness can run with the bash it already has.
 */
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = dirname(here);
const index = JSON.parse(readFileSync(join(pkg, "agent/index.json"), "utf8"));

const [verb, ...rest] = process.argv.slice(2);
const JSON_OUT = rest.includes("--json");
const args = rest.filter((a) => !a.startsWith("--"));

/* ---- find ------------------------------------------------------------ */

function find(query) {
  const words = query.toLowerCase().match(/[a-z][a-z-]{1,}/g) ?? [];
  const scored = index.entries
    .map((entry) => {
      let score = 0;
      for (const word of words) {
        if (entry.name.includes(word)) score += 8;
        if (entry.component.toLowerCase().includes(word)) score += 6;
        for (const term of entry.search) {
          if (term === word) score += 4;
          else if (term.startsWith(word) || word.startsWith(term)) score += 2;
        }
        if ((entry.summary ?? "").toLowerCase().includes(word)) score += 1;
      }
      /* A decision outranks a part at equal relevance: patterns answer first. */
      if (entry.kind === "pattern") score *= 1.25;
      return { entry, score };
    })
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 8);

  if (JSON_OUT) {
    console.log(JSON.stringify(scored.map((r) => r.entry), null, 2));
    return 0;
  }
  if (scored.length === 0) {
    console.log(
      `No component matches "${query}".\n\n` +
        "If this is a product question (page shape, save semantics, what a\n" +
        "destructive action feels like) the system has no answer yet. That is a\n" +
        "design decision: raise it rather than answering it at the call site.",
    );
    return 0;
  }
  for (const { entry } of scored) {
    const tag = entry.kind === "pattern" ? "pattern" : "primitive";
    console.log(`${entry.component}  (${tag})`);
    console.log(`  import  ${entry.import}`);
    if (entry.rules !== null) console.log(`  rules   design rules ${entry.name}`);
    if (entry.summary !== null) {
      console.log(`  what    ${entry.summary.slice(0, 180)}`);
    }
    console.log("");
  }
  return 0;
}

/* ---- rules ----------------------------------------------------------- */

function rules(name) {
  const entry =
    index.entries.find((e) => e.name === name) ??
    index.entries.find((e) => e.component.toLowerCase() === name.toLowerCase());
  if (entry === undefined) {
    console.error(`unknown component: ${name}  (try: design find ${name})`);
    return 1;
  }
  if (entry.rules === null) {
    console.log(
      `${entry.component} is a primitive and carries no rules of its own.\n` +
        `Source: ${entry.source}`,
    );
    return 0;
  }
  console.log(readFileSync(join(pkg, entry.rules), "utf8"));
  return 0;
}

/* ---- doctor ---------------------------------------------------------- */

/*
 * Each check is a regex over source text, which is the point: it runs with no
 * build, no type information and no bundler, so an agent can run it on a file
 * it has not finished writing. The app's own eslint config is the stricter
 * version of this and remains the real gate inside the monorepo.
 */
const CHECKS = [
  {
    id: "raw-color",
    level: "error",
    why: "Tokens are the only colours. A hex cannot flip between themes, so it is a light-mode-only component.",
    test: /(?:className|class)\s*=\s*["'][^"']*#[0-9a-fA-F]{3,8}|style\s*=\s*\{\{[^}]*#[0-9a-fA-F]{3,8}/g,
  },
  {
    id: "palette-class",
    level: "error",
    why: "Tailwind's own palette is not in this system. Use a semantic token: bg-panel, text-ink-subtle, border-line.",
    test: /\b(?:bg|text|border|ring|fill|stroke|from|to|via|divide|outline|shadow|decoration|accent|caret)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/g,
  },
  {
    id: "dark-variant",
    level: "error",
    why: "The theme flips token values; components never branch on it. A dark: variant is a second source of truth for one colour.",
    test: /\bdark:[a-z[]/g,
  },
  {
    id: "arbitrary-value",
    level: "warn",
    why: "An arbitrary value is a geometry decision taken at a call site. Spend a token, or add one.",
    test: /\b(?:w|h|min-w|min-h|max-w|max-h|p|px|py|pt|pr|pb|pl|m|mx|my|gap|text|rounded|size|top|left|right|bottom|inset|translate-x|translate-y)-\[[^\]]+\]/g,
  },
  {
    id: "hand-rolled-confirm",
    level: "warn",
    why: "A destructive action is ConfirmableAction, not a local arming boolean. Run: design rules confirmable-action",
    test: /const\s+\[(?:confirming|armed|isConfirming|showConfirm|pendingDelete)[,\]]/g,
  },
  {
    id: "copied-source",
    level: "error",
    why: "This file looks like a copy of a packaged component. Import it instead; a copy stops receiving the decisions it carries.",
    test: /^\s\*\sRules for [A-Z]/gm,
  },
];

function walk(input, out = []) {
  /*
   * Resolved, so the self-skip below compares like with like. Run as
   * `design doctor src` from inside this package it was comparing a relative
   * path against an absolute one, and flagged every packaged component as a
   * copy of itself.
   */
  const target = resolve(input);
  const stat = statSync(target);
  if (stat.isFile()) {
    if ([".ts", ".tsx", ".js", ".jsx"].includes(extname(target))) out.push(target);
    return out;
  }
  for (const name of readdirSync(target)) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    walk(join(target, name), out);
  }
  return out;
}

function doctor(targets) {
  const paths = targets.length > 0 ? targets : ["."];
  const findings = [];
  for (const target of paths) {
    if (!existsSync(target)) {
      console.error(`no such path: ${target}`);
      return 1;
    }
    for (const file of walk(target)) {
      if (file.includes(`${pkg}/src/`)) continue;
      const code = readFileSync(file, "utf8");
      const lines = code.split("\n");
      for (const check of CHECKS) {
        for (const match of code.matchAll(check.test)) {
          const line = code.slice(0, match.index).split("\n").length;
          findings.push({
            file: relative(process.cwd(), file),
            line,
            id: check.id,
            level: check.level,
            found: match[0].trim().slice(0, 70),
            why: check.why,
            source: (lines[line - 1] ?? "").trim().slice(0, 100),
          });
        }
      }
    }
  }

  if (JSON_OUT) {
    console.log(JSON.stringify({ findings, clean: findings.length === 0 }, null, 2));
  } else if (findings.length === 0) {
    console.log("clean: nothing in those files contradicts the system.");
  } else {
    const byCheck = new Map();
    for (const f of findings) {
      if (!byCheck.has(f.id)) byCheck.set(f.id, []);
      byCheck.get(f.id).push(f);
    }
    for (const [id, group] of byCheck) {
      console.log(`${group[0].level.toUpperCase()}  ${id}  (${group.length})`);
      console.log(`  ${group[0].why}`);
      for (const f of group.slice(0, 6)) {
        console.log(`    ${f.file}:${f.line}  ${f.found}`);
      }
      if (group.length > 6) console.log(`    ... ${group.length - 6} more`);
      console.log("");
    }
  }
  return findings.some((f) => f.level === "error") ? 1 : 0;
}

/* ---- dispatch -------------------------------------------------------- */

const USAGE = `@langchain/gtm-platform-design-system ${index.version}

  design find <words>        which component answers this job
  design rules <component>   the decisions that component carries
  design doctor [paths...]   check written code against the system

  --json                     machine-readable output

${index.counts.primitives} primitives, ${index.counts.patterns} patterns, ${index.counts.withRules} with rules.
Start at AGENTS.md in this package.`;

switch (verb) {
  case "find":
    process.exit(find(args.join(" ")));
  case "rules":
    process.exit(rules(args[0] ?? ""));
  case "doctor":
    process.exit(doctor(args));
  default:
    console.log(USAGE);
    process.exit(verb === undefined ? 0 : 1);
}
