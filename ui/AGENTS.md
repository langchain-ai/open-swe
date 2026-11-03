# Dashboard UI

Read [DESIGN.md](DESIGN.md) before changing Open SWE’s web/app UI.
Prefer the installed Macaw components and semantic tokens over custom controls and styles.

Before implementing or replacing a control, use Macaw CLI discovery from the repository root:

```sh
pnpm --dir ui exec macaw search "<capability>" --json
pnpm --dir ui exec macaw inspect <ComponentName> --json
```

Use the returned imports and read the installed component and styling guidance linked in DESIGN.md.
Discovery reads the installed catalog; do not assume APIs from another Macaw version.
Preserve the specialized integrations documented in DESIGN.md.

## Visual style

The dashboard aims for quiet, deliberate minimalism: structure comes from surface tone, not lines.

- Colors come from Macaw tokens only.
  The dark theme is Macaw's own `neutral` ramp; app-level adjustments live in `src/styles.css` as token references, never hand-picked hex values.
- Separate regions by stepping surfaces: page `bg-surface-level-1`, sidebar `bg-surface-level-2`, raised surfaces (composer, menus, user messages) `bg-elevated`.
  Do not add a border where a tone step already separates two regions.
- Borders have three weights:
  - `border-muted` for structural dividers: the sidebar edge, header bottoms, scroll edges.
  - `border-subtle` for quiet outlines.
  - `border-default` only for inputs and controls whose edge must read.
  In dark mode `--border-default` is lowered to `neutral-300` (`src/styles.css`) so control outlines stay soft too.
- The composer is a filled surface with no outline in dark mode (`border-subtle dark:border-transparent`); its chin tucks under it by the composer's corner radius so the chin's controls sit centered, with equal space above and below.
- Navigation rows are plain rows, not filled buttons; reserve filled emphasis for the primary action.
  When a Macaw `Button` stands in for a row, add `border-0`: its plain variant carries a 1px transparent border that shifts the label off the column.
- Row icons occupy the same 16px column so labels line up; check alignment by measuring positions, not by eye.
- Header rows that sit side by side share one height: the thread header, the right panel's tab bar (`--workspace-topbar-height`) and the title bar are all 44px (`h-11`).
  A control that moves between states, like the panel toggle, keeps the same spot in both.
- Disabled icon buttons only dim (`disabled:bg-transparent`); Macaw's disabled fill reads as a pressed button.
- Section view menus use the three-line filter icon (`FunnelSimpleIcon`) and show on hover; overflow menus such as the sidebar's More use three dots (`DotsThreeIcon`).
- A menu item that navigates away must not hand focus back to its trigger (`onCloseAutoFocus` → `preventDefault()`), or Chromium draws a focus ring on it.
  Escape and outside clicks still return focus.
- Decorative display headings, such as the empty-state "What should we work on?", and read-only labels inside controls, such as a started thread's project in the composer, are not selectable (`select-none cursor-default`).
- Navigation chrome is not selectable either: the sidebar frame sets `select-none`, so its rows, headers and labels never highlight on a double-click (text fields inside it stay editable).

## Desktop title bar

Electron applies `-webkit-app-region` in DOM order, so a later `drag` region swallows clicks on any earlier button it overlaps, and `drag` inherits to non-interactive descendants.
Buttons beside the traffic lights belong in `WindowControls` (`src/components/sidebar-layout.tsx`), which renders into the root layout's last-child `[data-desktop-window-controls]` host.
Never portal interactive controls into `<body>` over a draggable header: where a portal lands depends on mount timing.
