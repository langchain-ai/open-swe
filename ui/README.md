# Open SWE dashboard

React, TanStack Start, Tailwind CSS, and Base UI/shadcn components. This package
belongs to the repository's pnpm workspace; run these commands from `ui/`:

```sh
pnpm run dev
pnpm run typecheck
pnpm run test src/features/settings/components/WorkspaceMCPSection.test.tsx
pnpm run build
```

Use `make dev-ui` from the repository root to run the frontend and Python backend
together. See the root development guide for deployment and authentication.

## Design system

Open `/design-system` on the Vite development server to explore the actual
components in light and dark mode. The gallery works without a backend; its
forms use local state. Theme selection uses the dashboard's normal preference.
The gallery is unavailable in production, and its component bundle is excluded
from production builds.

| Layer | Location | Responsibility |
| --- | --- | --- |
| Foundations | `src/styles/tokens.css` | Semantic colors, radii, typography, animation tokens, and light/dark/agents theme values |
| Primitives | `src/components/ui/` | Buttons, inputs, menus, tooltips, cards, badges, alerts, and other reusable controls |
| Patterns | `src/components/patterns/` | Page headers, settings groups/rows/panels/navigation, and empty states |
| Shell | `src/components/AppShell.tsx` | Application sidebar and settings-page layout |
| Features | `src/features/` | Domain state, queries, permissions, and feature-specific components |

The system builds on the existing primitives. It is part of this package and
does not require a separate component library or Storybook installation.

### Compose a settings section

```tsx
import { SettingsRow, SettingsSection } from "@/components/patterns/settings"
import { Switch } from "@/components/ui/switch"

<SettingsSection title="Preferences" description="Defaults for new tasks.">
  <SettingsRow
    label="Notifications"
    description="Notify when an agent run finishes."
    htmlFor="notifications"
    control={
      <Switch
        id="notifications"
        checked={enabled}
        onCheckedChange={setEnabled}
      />
    }
  />
</SettingsSection>
```

`SettingsPanel` holds full-width editors and lists. `SettingsNavRow` accepts
`to`, optional route `params`, `label`, and `description` for navigation rows.
These components own presentation; the caller owns fetching and persistence.

### Compose a feature page

```tsx
import { PageHeader } from "@/components/patterns/page-header"
import { EmptyState } from "@/components/patterns/empty-state"
import { Button } from "@/components/ui/button"

<PageHeader
  title="Automations"
  size="compact"
  description="Run Open SWE on a recurring schedule."
  actions={<Button onClick={createAutomation}>New automation</Button>}
/>

<EmptyState
  title="No automations yet"
  description="Create an automation to get started."
  action={<Button onClick={createAutomation}>New automation</Button>}
/>
```

`PageHeader` renders the page's `h1`; use it once per page. Its default size is
the settings-page heading; `compact` matches workspace pages. `EmptyState`
supports an optional decorative `icon` and uses `h3` for its optional title.
Page-specific spacing belongs at the call site.

### Extend the system

Use semantic utilities such as `bg-card`, `text-muted-foreground`, and
`border-border`. Theme values live in one place; agent overrides retain the
`.agents-ui` and `html[data-agents-theme]` selectors so portalled menus and
tooltips receive the same theme. Agent behavior styles remain in
`src/styles/agents.css`.

Import primitives and patterns directly. Add a shared pattern when real screens
repeat the same structure, and keep domain logic in its feature. Prefer the
existing Button/Input/Select variants over restyling native controls at each
call site. Layout-specific `className` overrides are supported.

Update the gallery for new shared components. Verify keyboard access, labels,
focus, disabled/error states, light/dark mode, and narrow layouts. Run the
affected feature tests and typecheck; avoid tests that snapshot class strings.
