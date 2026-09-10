# AGENTS.md

This file applies to all work under `ui/`.

## Components and design system

- Start with the component map and examples in `README.md`. Preview existing
  components at `/design-system` while running the Vite development server.
- `src/styles/tokens.css` owns semantic theme values, Tailwind token mappings,
  and agents surface overrides. Use `bg-card`, `text-muted-foreground`,
  `border-border`, and other semantic utilities; do not introduce parallel color
  variables or duplicate light/dark palettes in feature stylesheets.
- `src/components/ui/` owns Base UI/shadcn primitives and their variants. Reuse
  these for buttons, inputs, switches, menus, tooltips, and other controls.
- `src/components/patterns/` owns shared presentation: page headers, settings
  sections/rows/panels/navigation, and empty states. Import directly from the
  relevant file. Patterns must not import feature modules, API clients, query
  hooks, session state, or `AppShell`.
- `src/components/AppShell.tsx` composes application navigation and page layout.
  Keep feature queries, permissions, domain copy, and state in `src/features/`.
- Extract a pattern when existing screens share a structure; keep one-off
  domain components in their feature. Extend an existing primitive's variants
  before creating another control with its own appearance and behavior.
- Associate labels with controls using `htmlFor` and `id`; give icon-only
  buttons an accessible name. Preserve Base UI keyboard and focus behavior.
- Add representative states to the development gallery when extending the
  shared system. Check light/dark themes, narrow layouts, disabled/error states,
  and portalled menus/tooltips. Test observable behavior rather than CSS strings
  or component source structure; run only tests related to the change.
