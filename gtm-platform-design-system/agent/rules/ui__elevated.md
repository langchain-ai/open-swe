# Elevated

Depth is computed, never picked: declare an offset, let the context resolve the level.

Import: `@langchain/gtm-platform-design-system/ui/elevated`
Source: `src/ui/elevated.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### Elevated

| Prop | Type | Required |
|---|---|---|
| `offset` | `number` | yes |
| `shadowLevel` | `number` | no |

## The decisions this carries

- Depth is computed, never picked: declare an offset, let the context resolve the level.
- Use shadow-control on a solid pressable that sits alone; shadow-raised-hover on primary hover only.
- Use shadow-popup on anchored floats (popover, menu, select list, hover card); shadow-overlay on dialogs and sheets.
- Anchored floats import POPUP_SURFACE_DENSE / PADDED / FLUSH / SHELL from popup-surface.ts — never hand-roll border+shadow-popup.
- Never shadow containment, outline/bordered pressables, segmented selected states, or ghost actions.
- One edge: the component paints ring or border; the shadow never paints a second ring.
- Never reach for stock Tailwind shadows (shadow-sm/md/lg); the four named rungs are the vocabulary.
- Depth responds to the press: what lifts on hover settles on active.
- The fill ladder stops at panel; above it, depth is shadow alone.
- bg-canvas
- bg-panel
- bg-panel
- bg-panel
- bg-panel
- bg-panel
- bg-panel

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
