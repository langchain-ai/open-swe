# Open SWE dashboard

TanStack Start + React 19, styled with the GTM Platform design system
(`@langchain/gtm-platform-design-system`, vendored at `../gtm-platform-design-system`).
Local setup lives in [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md).

## Building UI

Read [the design system's AGENTS.md](../gtm-platform-design-system/AGENTS.md) first.
Import primitives and patterns from their subpaths; never rebuild one locally:

```tsx
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { PageSection } from "@langchain/gtm-platform-design-system/patterns/page-frame"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { GitPullRequest } from "@/components/glyphs"
```

- Colour comes only from tokens (`bg-panel`, `text-ink-subtle`, `border-line`, `bg-risk-bg text-risk`).
- Type uses the six rungs: `text-meta`, `text-label`, `text-body`, `text-title`, `text-page`, `text-display`.
- Icons are `<Icon icon={Glyph} size="sm|md|lg|nav" />` with glyphs from `@/components/glyphs`, the one
  place the app imports Heroicons.
- App shells: `@/components/AppShell` (settings and workspace pages) and `AgentsShell` in
  `src/features/agents/components/AgentsSidebar.tsx` (the agents surface), both on the system's `AppShell`.

```bash
pnpm exec design find "confirm before deleting"   # which component answers this job
pnpm exec design rules confirmable-action         # the decisions it carries
pnpm exec design doctor src/                      # check what you wrote
```
