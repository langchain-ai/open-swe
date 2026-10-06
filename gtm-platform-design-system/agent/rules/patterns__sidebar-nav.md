# SidebarNav

Selection is a fill, ink and weight. Never a border, never a shadow, never a left bar. A selected row is the same height in the same place with `bg-selected` behind it, at `text-ink` on the medium step, so the rail does not reflow and no...

Import: `@langchain/gtm-platform-design-system/patterns/sidebar-nav`
Source: `src/patterns/sidebar-nav.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### SidebarNavRow

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `href` | `string` | no |
| `onSelect` | `() => void` | no |
| `selected` | `boolean` | no |
| `count` | `number` | no |
| `countOverflow` | `boolean` | no |
| `attentionDot` | `boolean` | no |

### SidebarNavChild

| Prop | Type | Required |
|---|---|---|
| `icon` | `Glyph` | no |

### SidebarNavItem

| Prop | Type | Required |
|---|---|---|
| `icon` | `Glyph` | yes |
| `children` | `ReactNode` | no |
| `compact` | `boolean` | no |
| `preview` | `ReactNode` | no |
| `onPreviewOpenChange` | `(open: boolean) => void` | no |
| `open` | `boolean` | no |
| `onOpenChange` | `(open: boolean) => void` | no |

### SidebarNavGroup

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `count` | `number` | no |
| `open` | `boolean` | yes |
| `onOpenChange` | `(open: boolean) => void` | yes |
| `children` | `ReactNode` | yes |

### SidebarNavSection

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |

### SidebarNavCluster

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `label` | `string \| null` | no |
| `divider` | `boolean` | no |

### SidebarNav

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `children` | `ReactNode` | yes |
| `flush` | `boolean` | no |

## The decisions this carries

- Selection is a fill, ink and weight. Never a border, never a shadow, never a left bar. A selected row is the same height in the same place with `bg-selected` behind it, at `text-ink` on the medium step, so the rail does not reflow and no second indicator has to be kept in sync with the first. The type half is not decoration: `--gtm-selected` and `--gtm-hover` resolve to the same value on the dark theme, so a row that said selection with the fill alone said nothing there.
- A selected row does not answer the pointer. Hover and selected are mutually exclusive class sets, not two layers: `hover:bg-hover` outranks `bg-selected` on specificity, so a row that carried both repainted as merely-hovered the moment a pointer crossed the thing you were standing on.
- Two rungs, and only two destination sizes: parent rows are 32px on radius 14, child rows are 28px on radius 12. Both take the same 10px inset, so height and position are the only difference between them. The child's radius is derived, not copied: half of 28 is 14, and the ratio law calls a radius at or above half the smaller dimension a circle, so `compact` is the rung and `control` would be a pill. A family that will grow (Admin, later Team) takes `SidebarNavCluster`'s `label`, the third size: meta, ink-subtle, not pressable, never an icon, never a destination.
- Parents carry icons. Children carry the spine, and may also carry a 16px glyph when the destination needs its own mark (Alerts, Meetings, …). The spine still says nesting; the optional child icon names the job. The list inside the spine is padded 8px so the child's fill clears the hairline rather than landing on it.
- The reserved 20px trailing lane carries one quiet attention signal. A numeral means unresolved, actionable work and does not clear merely because the destination opened. A 6px primary dot means new content since the server-owned acknowledgement and clears when the destination is opened. Zero, loading and unavailable render nothing. Never aggregate a child's signal onto its parent: duplicate totals create noise without adding information.
- Counts are numerals on tabular figures, never chips. The chip's fill was the selected fill, so a count disappeared into the row it most needed to be legible on. Counts publish 99+ rather than growing the lane. In a collapsed icon rail any attention compresses to the same 6px dot; the expanded rail and contextual preview carry the exact count.
- Depth stops here. The rail expresses one level of nesting and no more; anything deeper is an object route, and depth on an object route is the lineage band's job (see PageBand). A third level in the sidebar is the signal that a surface needed a band and did not get one.
- A destination never hides *itself*. The product rail keeps children visible; omit `open` / `onOpenChange` so the spine stays mounted. Disclosure belongs to `SidebarNavGroup` on an index rail, not to the product map. A heading-only family (Admin) is a section label plus destinations, never a parent row that only exists to hold one child. Role-gated families follow a hairline after daily work so they can grow without joining Overview.
- A group's chevron IS its leading glyph, and its state is controlled by the surface. The 16px slot holds one thing: a destination spends it on its glyph, a disclosure spends it on its chevron, and neither grows a second leading lane. Open state lives with whoever owns the URL, so a pasted link can decide what is expanded.
- The agent is a sidebar row plus Cmd-J, never a header button. It was already duplicated by the rail, and the button was what made a permanent header look necessary.
- Icons here are navigation, not decoration: one glyph per destination, at the 16px slot, from the closed glyph list. A rail where some parents have icons and others do not is a rail with two lanes.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
