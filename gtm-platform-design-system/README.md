# @langchain/gtm-platform-design-system

The GTM Platform internal design system, created by Amal Irgashev, packaged:
token layer, 73 primitives, 41 patterns, and the product decisions the patterns
carry.

**Agents start at [AGENTS.md](AGENTS.md).** It is the short version of this file
with the three commands and the four non-negotiable rules.

## Install

```bash
pnpm add @langchain/gtm-platform-design-system
```

```css
/* your root stylesheet */
@import "tailwindcss";
@import "@langchain/gtm-platform-design-system/styles.css";
@source "../node_modules/@langchain/gtm-platform-design-system/src";
```

```tsx
/* your root component */
import { DesignSystemProvider } from "@langchain/gtm-platform-design-system/host";

<DesignSystemProvider link={YourRouterLink}>{children}</DesignSystemProvider>
```

Already running shadcn/ui? One more line re-skins the components you already
have, in place, with no edits to any of them:

```css
@import "@langchain/gtm-platform-design-system/bridge.css";
```

Requires Tailwind v4, React 19, and `@base-ui/react`. The optional dependencies
are per-component: `@tanstack/react-table` and `@tanstack/react-virtual` for the
data grid, `cmdk` for the command surface, `date-fns` and `react-day-picker` for
the date controls, `sonner` for toasts. A consumer who never imports those
components never needs them.

## What you get

| | Count | |
|---|---|---|
| Primitives | 73 | parts: `Button`, `Box`, `Dialog`, `Combobox`, `Icon` |
| Patterns | 41 | decisions: `PageFrame`, `FilterableTable`, `ConfirmableAction`, `AppShell` |
| With explicit rules | 43 | the rule array is in the source and published to `agent/rules/` |
| Tokens | 205 lines | one name, two values; the theme flips the value |

The patterns are the point. A component library hands you parts; this answers
recurring product questions once so no builder, human or agent, answers them
again from scratch. `agent/CATALOG.md` is the full list.

## Commands

```bash
npx design find "confirm before deleting"   # which component answers this job
npx design rules confirmable-action         # the decisions it carries
npx design doctor src/                      # check written code, --json for agents
```

`doctor` needs no build and no types, so it runs on a half-written file. It
catches the four things types cannot see: a hardcoded colour, a Tailwind palette
class, a `dark:` variant, and a hand-rolled confirm dialog.

## How this is maintained

The GTM app is the source today, and this package is generated from it:

```bash
pnpm extract   # re-run the extraction, regenerate the index and styles
pnpm verify    # fail if the package has drifted from the app (CI runs this)
```

`scripts/extract.mjs` resolves the dependency closure from the two component
directories, pulls in what they reach, and refuses to publish a module that does
not build. Three things stop it, each reported rather than guessed: a product
edge excludes the file with a reason, a shim substitutes this package's own
standalone version, and a framework import is rewritten onto the host adapter.
`agent/EXCLUDED.md` is the published list of what stayed behind.

The generation is temporary, not the end state. Once a second consumer exists
this should become its own repository with both teams owning it; until then the
drift check is what keeps one copy honest.

## Boundaries

Four host slots, and no more: `link`, `image`, `theme`, `brandMark`. A component
that needs a fifth is asking the design system to know something about your
product, which is the boundary the host adapter exists to hold.
