# @langchain/gtm-platform-design-system

The GTM Platform internal design system, created by Amal Irgashev.

A design system you install rather than imitate. 73 primitives, 41 patterns, one
token layer, and the product decisions that go with them.

The distinction that matters: a **primitive** is a part, and a **pattern** is a
decision somebody already made. Most of the value here is in the patterns, and
the rules they carry are the reason this is a design system rather than a
component folder. Read a pattern's rules before you use it.

## First: three commands

```bash
npx design find "confirm before deleting"   # which component answers this job
npx design rules confirmable-action         # the decisions it carries
npx design doctor src/                      # check what you wrote
```

`find` is the one to reach for when you know the job but not the name, which is
most of the time. Add `--json` to any of them.

Run `doctor` on the files you touched before you say you are done. It needs no
build and no types, so it works on a half-written file, and it catches the four
things that types cannot: a hardcoded colour, a Tailwind palette class, a `dark:`
variant, and a hand-rolled confirm dialog.

## The rules that are not negotiable

1. **Tokens are the only colours.** `bg-panel`, `text-ink-subtle`,
   `border-line`. Never a hex, never `bg-gray-100`, never `dark:`. One name, two
   values: the theme flips the value and the component never knows.
2. **Install, do not imitate.** If a pattern exists, import it. A copied
   component stops receiving the decision it carries, and the second copy is
   where the two surfaces start disagreeing.
3. **Compose layout through `Box` / `Stack` / `Inline`.** `padding="md"` is
   expressible; `p-[13px]` is a geometry decision taken at a call site.
4. **A product question no pattern answers is a design decision.** Page shape,
   save semantics, what a destructive action feels like. Surface it. Point at the
   nearest shipped precedent in the meantime and say in the PR that you did.
   Do not answer it locally, and never add a second pattern answering a question
   an existing pattern already answers.
5. **One icon set, one wrapper.** `<Icon icon={Mail} size="md" />` from
   `ui/glyphs`. The size picks the drawn set, which is why call sites never name
   one.

## Where things are

Four levels, each one cheaper than the next, so you never read more than the
answer costs:

| You want | Read |
|---|---|
| Which component | `npx design find "<the job>"`, or grep `agent/index.json` |
| The whole list | `agent/CATALOG.md` |
| One decision | `agent/rules/<dir>__<name>.md`, or `npx design rules <name>` |
| A signature | `agent/props.json`, keyed by component, or the Props table in its rules file |
| Behaviour | `src/<dir>/<name>.tsx` — the source is the last word |
| A worked example | `agent/recipes/` |

`agent/index.json` is the machine-readable catalog: every entry has its import
path, its exports, its rules file, and search terms. It is generated from the
source, so it cannot describe a component that does not exist.

Check the signature before you write the call site. Prop names here are not
guessable from the concept: `Box` paints with `bg`, not `fill`, and
`StateNotice` takes `tone="ATTENTION"` in caps, because enum values are
ALL_CAPS throughout. React renders an unknown prop as a DOM attribute without
complaining, so a wrong name is a surface that silently loses its background
rather than an error you will see.

## Install

```bash
pnpm add @langchain/gtm-platform-design-system
```

One import in your root stylesheet, after Tailwind:

```css
@import "tailwindcss";
@import "@langchain/gtm-platform-design-system/styles.css";

/* Tailwind must see the class names inside the package. */
@source "../node_modules/@langchain/gtm-platform-design-system/src";
```

Already have shadcn/ui components? Add one more line and they re-skin in place,
with no component edits:

```css
@import "@langchain/gtm-platform-design-system/bridge.css";
```

The bridge re-points shadcn's own variables (`--background`, `--primary`,
`--sidebar`, `--destructive`) at these tokens. A Tailwind v4 shadcn app indirects
every utility through a bare custom property, so redefining that one layer
re-themes components this package never shipped you.

## The host adapter

This package knows nothing about your application, and there are exactly four
slots where it finds out. Mount the provider once, at your root:

```tsx
import { DesignSystemProvider } from "@langchain/gtm-platform-design-system/host";
import { Link } from "@tanstack/react-router";

<DesignSystemProvider link={Link} brandMark={MyMark}>
  {children}
</DesignSystemProvider>
```

| Slot | For | Default if omitted |
|---|---|---|
| `link` | your router's link component | a plain `<a>` |
| `image` | an image component | a plain `<img>` |
| `theme` | `{ theme, setTheme }` if you already own it | this package drives `data-theme` and `.dark` on `<html>` |
| `brandMark` | your mark, where a pattern reserves a lane | nothing renders |

A component that needs a fifth slot is asking the design system to know
something about your product. That is the boundary this module exists to hold.

## Theming

Tokens live on `:root` and flip under `[data-theme="dark"]`. The dark variant
also matches `.dark`, so a consumer already driving shadcn's class-based dark
mode needs to change nothing.

State colours come as a family with a guarantee: every ink sits at exactly 7.0:1
on its own tint, all five share one chroma and one lightness, and hue is the only
variable. That is what makes a row of mixed badges read at one weight. It only
holds if you keep the pairs together — `bg-positive-bg text-positive`, never one
with the other's partner.

## What is not here, and why

Sixteen components stayed behind. Each is listed with its reason in
`agent/EXCLUDED.md`. They fall into two groups: product surfaces that reach into
routes or app state and were only ever patterns by filing, and brand assets that
belong to the application rather than the system.

Three patterns are worth asking for if you need them —
`AgentCompanionChrome`, `AgentDockSlot`, `AgentLayoutMenu` — they are the agent
shell, and they need their session state injected as props before they can
travel. That is a known piece of work, not a refusal.
