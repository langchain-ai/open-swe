/*
 * Builds the agent-facing surface of the package.
 *
 * The app's own component reference is a React route behind a search box: a
 * human opens it, filters, and toggles the rules on. An agent cannot see any of
 * that, so every answer that gallery gives has to exist as a file too, derived
 * from the same source rather than written twice. That is what this emits.
 *
 * It is deliberately data and prose, not tools. An agent already knows how to
 * read a JSON index and grep a markdown file; a `find_component` tool would be
 * a worse version of ripgrep that only works inside one harness. The levels are
 * sized for progressive disclosure: AGENTS.md is the front door, CATALOG.md is
 * the index, agent/rules/<name>.md is one decision, and the source file is the
 * last word.
 */
import {
  readFileSync,
  writeFileSync,
  readdirSync,
  mkdirSync,
  rmSync,
  existsSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = dirname(here);
const srcDir = join(pkg, "src");
const agentDir = join(pkg, "agent");
const VERIFY = process.argv.includes("--verify");

const KINDS = [
  { dir: "ui", kind: "primitive", label: "Primitives" },
  { dir: "patterns", kind: "pattern", label: "Patterns (decisions)" },
  { dir: "data-grid", kind: "primitive", label: "Data grid" },
];

/* Words that say nothing about which component to reach for. */
const STOP = new Set(
  ("a an and are as at be been but by can cannot does for from has have how in" +
    " into is it its never no not of on one only or our out so than that the" +
    " their then there these this to two under until use used uses using what" +
    " when where which while who why with without you your always every both" +
    " each own same second third keep keeps kept take takes make makes made" +
    " more most less least they them if else once also just like own rules" +
    " rule retokenized core wiki pattern patterns component components file" +
    " files thing things way ways place places left right own set sets gtm" +
    " rep reps something anything nothing never always behalf onto via per" +
    " any all none other others itself himself herself themselves").split(" "),
);

function pascal(name) {
  return name
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

/*
 * The first prose paragraph of the leading block comment, as one line.
 *
 * A pattern's comment opens with "Rules for X." and then explains why the file
 * exists, so a header-shaped first paragraph is skipped rather than reported as
 * the summary: "Rules for ConfirmableAction" tells a reader deciding what to
 * reach for exactly nothing.
 */
function leadDoc(code) {
  const match = code.match(/\/\*\n([\s\S]*?)\*\//);
  if (match === null) return null;
  const lines = match[1]
    .split("\n")
    .map((line) => line.replace(/^\s*\*\s?/, "").trimEnd());

  const paragraphs = [];
  let current = [];
  for (const line of lines) {
    if (line.trim().length === 0) {
      if (current.length > 0) paragraphs.push(current.join(" ").trim());
      current = [];
      continue;
    }
    current.push(line.trim());
  }
  if (current.length > 0) paragraphs.push(current.join(" ").trim());

  const useful = paragraphs.filter(
    (p) => p.length > 0 && !/^Rules for [A-Za-z/ `]+\.?$/.test(p),
  );
  return useful[0] ?? null;
}

function exportsOf(code) {
  const names = new Set();
  for (const m of code.matchAll(/^export\s+(?:async\s+)?function\s+([A-Za-z0-9_]+)/gm)) {
    names.add(m[1]);
  }
  for (const m of code.matchAll(/^export\s+(?:const|let|class)\s+([A-Za-z0-9_]+)/gm)) {
    names.add(m[1]);
  }
  for (const m of code.matchAll(/^export\s+\{([^}]+)\}/gm)) {
    for (const piece of m[1].split(",")) {
      const name = piece.trim().split(/\s+as\s+/).pop()?.trim();
      if (name !== undefined && name.length > 0 && !name.startsWith("type ")) {
        names.add(name);
      }
    }
  }
  return [...names].filter((n) => !/^[A-Z_]+_RULES$/.test(n)).sort();
}

/*
 * The rules are already an exported array in the source, which the gallery
 * renders verbatim. Reading them from there rather than from a comment keeps
 * one copy: a rule edited in code is a rule edited here.
 */
function rulesOf(code) {
  const match = code.match(
    /(?:const|export const)\s+[A-Z0-9_]+_RULES[^=]*=\s*\[([\s\S]*?)\n\];/,
  );
  if (match === null) return [];
  const body = match[1];
  const rules = [];
  for (const m of body.matchAll(/"((?:[^"\\]|\\.)*)"/g)) {
    rules.push(m[1].replace(/\\"/g, '"').replace(/\\n/g, " "));
  }
  return rules;
}

/*
 * The props of each exported component, read off its own `*Props` declaration.
 *
 * This exists because of a real miss: a consumer wrote `<Box fill="panel">`,
 * which React happily rendered as an unknown DOM attribute, because the prop
 * is `bg`. Nothing failed, nothing typechecked (it was a probe), and the
 * surface silently lost its background. A name and a summary are not enough to
 * write a call site; the index has to carry the signature too.
 */
function propsOf(code) {
  const out = {};
  const declarations = code.matchAll(
    /(?:interface|type)\s+([A-Za-z0-9_]*Props)[^{]*\{([\s\S]*?)\n\}/g,
  );
  for (const [, owner, body] of declarations) {
    const props = [];
    let doc = null;
    for (const raw of body.split("\n")) {
      const line = raw.trim();
      const inline = line.match(/^\/\*\*\s*(.*?)\s*\*\/$/);
      if (inline !== null) {
        doc = inline[1];
        continue;
      }
      if (line.startsWith("*") || line.startsWith("/*") || line.startsWith("//")) continue;
      const member = line.match(/^(readonly\s+)?([A-Za-z_$][\w$]*|"[^"]+")(\?)?:\s*(.+?);?$/);
      if (member === null) continue;
      const [, , propName, optional, type] = member;
      props.push({
        name: propName.replace(/"/g, ""),
        required: optional === undefined,
        type: type.replace(/;$/, "").trim().slice(0, 120),
        ...(doc === null ? {} : { doc }),
      });
      doc = null;
    }
    if (props.length > 0) out[owner.replace(/Props$/, "")] = props.slice(0, 24);
  }
  return out;
}

function searchTerms(name, doc, rules, exported) {
  const bag = new Map();
  const add = (text, weight) => {
    for (const raw of String(text).toLowerCase().match(/[a-z][a-z-]{2,}/g) ?? []) {
      const word = raw.replace(/-+$/, "");
      if (STOP.has(word) || word.length < 3) continue;
      bag.set(word, (bag.get(word) ?? 0) + weight);
    }
  };
  add(name.replace(/-/g, " "), 6);
  /*
   * Export names are split on their camel humps. `FilterableTableGroupControl`
   * as one token matches nothing a person would type; "group" and "control" do.
   */
  add(
    exported
      .map((n) => n.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/_/g, " "))
      .join(" "),
    4,
  );
  add(doc ?? "", 2);
  add(rules.slice(0, 3).join(" "), 1);
  return [...bag.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 12)
    .map(([word]) => word);
}

const entries = [];
for (const { dir, kind } of KINDS) {
  const full = join(srcDir, dir);
  if (!existsSync(full)) continue;
  for (const file of readdirSync(full).sort()) {
    if (!/\.(tsx|ts)$/.test(file)) continue;
    /* Test helpers are not part of the system's surface. */
    if (file.includes(".test-utils.")) continue;
    const name = file.replace(/\.(tsx|ts)$/, "");
    const code = readFileSync(join(full, file), "utf8");
    const exported = exportsOf(code);
    if (exported.length === 0) continue;
    const doc = leadDoc(code);
    const rules = rulesOf(code);
    /*
     * A pattern's first rule is written to be the one a builder reads first, so
     * it is a better summary than the file's commentary about itself.
     */
    /*
     * Capped, because the index is meant to be read whole and the full text is
     * one `design rules` away. An uncapped first rule ran to 900 characters.
     */
    const headline = rules[0] ?? doc;
    const summary =
      headline === null || headline.length <= 240
        ? headline
        : `${headline.slice(0, 237).trimEnd()}...`;
    entries.push({
      name,
      component: pascal(name),
      kind,
      import: `@langchain/gtm-platform-design-system/${dir}/${name}`,
      source: `src/${dir}/${file}`,
      rules: rules.length > 0 ? `agent/rules/${dir}__${name}.md` : null,
      exports: exported,
      summary,
      ruleCount: rules.length,
      props: propsOf(code),
      search: searchTerms(name, doc, rules, exported),
      note: rules.length > 0 ? doc : null,
      _rules: rules,
      _dir: dir,
    });
  }
}

/* ---- src/index.ts ---------------------------------------------------- */

/*
 * The barrel names every export explicitly rather than star-re-exporting.
 * A star barrel over 115 modules silently collides: `Box` and `Calendar` are
 * each defined in two places here, and TypeScript resolves that by exporting
 * neither. First definition wins, the loser is recorded, and the collision is
 * visible in the generated file instead of as a missing export at a call site.
 */
const owner = new Map();
const collisions = [];
/*
 * The glyph barrel is considered last. It exports an icon per glyph, and some of
 * those names (`Box`, `Calendar`, `Orb`) are also components: an icon should
 * never win that collision, whatever the alphabet says.
 */
const byPrecedence = [
  ...entries.filter((e) => e.name !== "glyphs"),
  ...entries.filter((e) => e.name === "glyphs"),
];
for (const entry of byPrecedence) {
  for (const name of entry.exports) {
    const held = owner.get(name);
    if (held === undefined) {
      owner.set(name, entry);
    } else if (held !== entry) {
      collisions.push(`${name}: ${held._dir}/${held.name} wins over ${entry._dir}/${entry.name}`);
    }
  }
}

const perModule = new Map();
for (const [name, entry] of owner) {
  const key = `./${entry._dir}/${entry.name}`;
  if (!perModule.has(key)) perModule.set(key, []);
  perModule.get(key).push(name);
}

const barrel = [
  "/*",
  " * Convenience barrel. Generated by scripts/build-index.mjs.",
  " *",
  " * Prefer the subpath (`@langchain/gtm-platform-design-system/patterns/page-frame`) in application",
  " * code: it is what keeps a bundle honest, and it is what agent/index.json",
  " * hands an agent. This exists for exploration and for a consumer whose",
  " * bundler already tree-shakes ESM re-exports.",
  " *",
  collisions.length === 0
    ? " * No name is defined twice."
    : " * Names defined in more than one module, resolved first-wins:",
  ...collisions.map((line) => ` *   ${line}`),
  " */",
  "",
  'export * from "./host";',
  ...[...perModule.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([mod, names]) => `export { ${names.sort().join(", ")} } from "${mod}";`),
  "",
].join("\n");

/* ---- agent/index.json ----------------------------------------------- */

const index = {
  $schema: "https://langchain.dev/schemas/design-index-1.json",
  package: "@langchain/gtm-platform-design-system",
  title: "The GTM Platform internal design system",
  author: "Amal Irgashev",
  version: JSON.parse(readFileSync(join(pkg, "package.json"), "utf8")).version,
  generated: "scripts/build-index.mjs",
  howToUse: [
    "Grep `search` for the job you are doing before writing any component.",
    "Read `rules` for the chosen entry; those are decisions, not suggestions.",
    "Signatures are in agent/props.json, keyed by component, or in the rules file.",
    "Read `source` last, for props. Never copy a component into the consumer repo.",
    "No entry fits a product question? That is a design decision. Surface it, do not invent it.",
  ],
  counts: {
    primitives: entries.filter((e) => e.kind === "primitive").length,
    patterns: entries.filter((e) => e.kind === "pattern").length,
    withRules: entries.filter((e) => e.ruleCount > 0).length,
  },
  entries: entries.map(({ _rules, _dir, props, ...rest }) => ({
    ...rest,
    propCount: Object.values(props).reduce((total, list) => total + list.length, 0),
  })),
};

/*
 * Props live in their own file. Folded into the index they tripled it to 221KB,
 * which is a file an agent pays ~55k tokens to grep; the index has to stay
 * cheap enough to read whole, and a signature is only wanted once a component
 * has been chosen.
 */
const props = {
  package: index.package,
  title: index.title,
  author: index.author,
  version: index.version,
  note: "Signatures per component. The index is agent/index.json; the rules file for a component repeats its table in prose.",
  components: Object.fromEntries(
    entries
      .filter((e) => Object.keys(e.props).length > 0)
      .map((e) => [e.component, { import: e.import, props: e.props }]),
  ),
};

/* ---- agent/rules/*.md ----------------------------------------------- */

const ruleFiles = new Map();
for (const entry of entries) {
  if (entry._rules.length === 0) continue;
  ruleFiles.set(
    `${entry._dir}__${entry.name}.md`,
    [
      `# ${entry.component}`,
      "",
      entry.summary ?? "",
      "",
      `Import: \`${entry.import}\``,
      `Source: \`${entry.source}\``,
      `System: ${index.title}, created by ${index.author}.`,
      "",
      ...(Object.keys(entry.props).length === 0
        ? []
        : [
            "## Props",
            "",
            ...Object.entries(entry.props).flatMap(([owner, props]) => [
              `### ${owner}`,
              "",
              "| Prop | Type | Required |",
              "|---|---|---|",
              ...props.map(
                (prop) =>
                  `| \`${prop.name}\` | \`${prop.type.replace(/\|/g, "\\|")}\` | ${prop.required ? "yes" : "no"} |`,
              ),
              "",
            ]),
          ]),
      "## The decisions this carries",
      "",
      ...entry._rules.map((rule) => `- ${rule}`),
      "",
      "These are the shipped rules, read from the source's own exported rule",
      "array. A product question they do not answer is a design decision: raise",
      "it rather than answering it at the call site.",
      "",
    ].join("\n"),
  );
}

/* ---- agent/CATALOG.md ----------------------------------------------- */

const catalog = [
  "# Catalog",
  "",
  `${index.title}, created by ${index.author}.`,
  "",
  `${index.counts.primitives} primitives, ${index.counts.patterns} patterns, ` +
    `${index.counts.withRules} carrying explicit rules. Generated; do not edit.`,
  "",
  "A primitive is a part. A pattern is a decision already made, and the rules",
  "file is where that decision is stated. Reach for a pattern first.",
  "",
  "`agent/index.json` is the same data with search terms and export lists, which",
  "is the one to grep when you know the job but not the name.",
  "",
];
for (const { dir, label } of KINDS) {
  const group = entries.filter((e) => e._dir === dir);
  if (group.length === 0) continue;
  catalog.push(`## ${label}`, "");
  catalog.push("| Component | Import | Rules | What it is |");
  catalog.push("|---|---|---|---|");
  for (const e of group) {
    const summary = (e.summary ?? "").replace(/\|/g, "\\|").slice(0, 150);
    const rules = e.ruleCount > 0 ? `[${e.ruleCount}](${e.rules})` : "—";
    catalog.push(
      `| \`${e.component}\` | \`${dir}/${e.name}\` | ${rules} | ${summary} |`,
    );
  }
  catalog.push("");
}

/* ---- write ----------------------------------------------------------- */

const files = new Map([
  [join(srcDir, "index.ts"), barrel],
  [join(agentDir, "index.json"), `${JSON.stringify(index, null, 2)}\n`],
  [join(agentDir, "props.json"), `${JSON.stringify(props, null, 2)}\n`],
  [join(agentDir, "CATALOG.md"), catalog.join("\n")],
  ...[...ruleFiles].map(([name, body]) => [join(agentDir, "rules", name), body]),
]);

let stale = false;
if (!VERIFY) {
  rmSync(join(agentDir, "rules"), { recursive: true, force: true });
  mkdirSync(join(agentDir, "rules"), { recursive: true });
}
for (const [target, body] of files) {
  if (VERIFY) {
    const have = existsSync(target) ? readFileSync(target, "utf8") : null;
    if (have !== body) {
      console.error(`DRIFT ${target.replace(pkg + "/", "")}`);
      stale = true;
    }
  } else {
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, body);
  }
}
console.log(
  JSON.stringify(
    { ...index.counts, ruleFiles: ruleFiles.size, entries: entries.length },
    null,
    2,
  ),
);
if (stale) process.exit(1);
