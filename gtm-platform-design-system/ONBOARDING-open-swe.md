# Onboarding Open SWE

`@langchain/gtm-platform-design-system` is the GTM Platform internal design system, created by Amal
Irgashev. This is how to adopt it.

Written against `langchain-ai/open-swe` at `ui/` (Vite, TanStack Start, React 19,
Tailwind v4, Base UI, shadcn CLI 4.x). The stacks line up unusually well: same
Tailwind engine, same primitive library, and `registries` in your
`components.json` is already empty and waiting.

## The whole change

**1. Add the dependency.**

```bash
pnpm add @langchain/gtm-platform-design-system --filter open-swe-dashboard
pnpm add @heroicons/react --filter open-swe-dashboard
```

**2. Two lines in `ui/src/styles.css`,** after the existing Tailwind import and
before `./styles/agents.css`:

```css
@import "@langchain/gtm-platform-design-system/styles.css";
@import "@langchain/gtm-platform-design-system/bridge.css";
@source "../node_modules/@langchain/gtm-platform-design-system/src";
```

The bridge is what makes this a two-line adoption. Your `@theme inline` block
already indirects every utility through a bare custom property, so redefining
that layer re-skins all 35 primitives in `ui/src/components/ui/` at once,
without touching one of them.

**3. Mount the host provider** where your router renders, in `ui/src/router.tsx`:

```tsx
import { DesignSystemProvider } from "@langchain/gtm-platform-design-system/host";
import { Link } from "@tanstack/react-router";

<DesignSystemProvider link={Link}>{children}</DesignSystemProvider>
```

**4. Point your agent at it.** One line in `ui/AGENTS.md`:

```md
UI work uses @langchain/gtm-platform-design-system. Read node_modules/@langchain/gtm-platform-design-system/AGENTS.md
before writing any component, and run `npx design doctor <files>` before
claiming a UI change is done.
```

That is the adoption. Nothing below is required to start.

## Three things to decide, not discover

**Dark mode is already handled, but check it.** You drive `dark` as a class
(`@custom-variant dark (&:is(.dark *))`); the token sheet keys on
`[data-theme="dark"]`. The variant shipped here matches both, and the fallback
theme controller sets both. If you keep your own theme state, pass it as the
`theme` slot and nothing in this package touches the DOM.

**Icons: you run Phosphor, this runs Heroicons solid.** Both work in one app,
but two icon sets in one interface is a design smell, and the sets are drawn to
different rules. The package's `Icon` gate takes a glyph as a triple
(16/20/24 drawn separately, so a filled glyph does not clot at small sizes),
which is why Heroicons is a real peer dependency rather than a swappable alias.
Either adopt Heroicons for shipped components and keep Phosphor for your own, or
tell us and we will build the Phosphor glyph map — it is one file.

**State colours will change how your badges look, and that is the upgrade.**
`--success`, `--warning`, `--destructive` and `--info` come from a family with a
guarantee your current `mist` base does not make: every ink sits at exactly
7.0:1 on its own tint, all five share one chroma and one lightness, and hue is
the only variable. A row of mixed badges reads at one weight. It only holds if
the pairs stay together, and the bridge exposes both halves — `--destructive`
with `--destructive-foreground` for a solid button, `--destructive-tint` with
`--destructive-ink` for a badge. Mixing one with the other's partner is what
breaks it.

## What maps onto what you already have

Your `AppShell`, `AppSidebar` and `AppCommandPalette` are hand-rolled answers to
questions this package already answers, and the patterns below are the ones worth
reading first because your product has the same shape:

| Yours | Pattern | The decision it carries |
|---|---|---|
| `AppShell`, `sidebar-layout` | `AppShell` | three shell modes; a header is earned per route, never inherited |
| `AppSidebar` | `SidebarNav`, `SidebarTree` | row rungs, the count lane, what selection is made of |
| thread list | `AgentThreadRail` | the rail replaces navigation in focus mode; status on the row, never as a group |
| transcript | `AgentThread` | the 704px measure, and progress versus durable work |
| `TablePagination` | `FilterableTable` | the whole table surface: server filtering, toolbar, group rows |
| `AppCommandPalette` | `CommandPalette` | stayed behind: it binds GTM account data. Worth porting with us |

Start with `npx design find "<the job>"` rather than this table. The table goes
stale; the index is generated.

## Three patterns you will want that are not here yet

`AgentCompanionChrome`, `AgentDockSlot` and `AgentLayoutMenu` are the agent dock
and layout menu. They read the GTM session store directly, so they need that
state injected as props before they can travel. It is a known piece of work
rather than a refusal — ask and it moves up.

## What this does not give you

The rules are text; nothing enforces them in your repo. `doctor` is the cheap
version and it runs anywhere, but the real gate in the GTM monorepo is an eslint
config that resolves every class against the live theme. If adoption sticks,
that config is the next thing to package.
