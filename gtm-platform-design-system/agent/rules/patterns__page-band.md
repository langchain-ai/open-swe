# PageBand

Chrome may only assert what the content cannot: where you are (the sidebar), how deep you are (a lineage band), what you can do (the page toolbar). Zero, one, or one. Never two.

Import: `@langchain/gtm-platform-design-system/patterns/page-band`
Source: `src/patterns/page-band.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### PageBandLineage

| Prop | Type | Required |
|---|---|---|
| `variant` | `"lineage"` | yes |
| `lineage` | `readonly LineageCrumb[]` | yes |
| `onBack` | `() => void` | no |
| `backLabel` | `string` | no |
| `action` | `ReactNode` | no |

### PageBandToolbar

| Prop | Type | Required |
|---|---|---|
| `variant` | `"toolbar"` | yes |
| `children` | `ReactNode` | yes |
| `edge` | `"line" \| "none"` | no |

## The decisions this carries

- Chrome may only assert what the content cannot: where you are (the sidebar), how deep you are (a lineage band), what you can do (the page toolbar). Zero, one, or one. Never two.
- The band is always 44px at the same y, so the content edge never jumps between pages. That is why the height is not a prop: a band that measured itself from its contents would move the first row of every page it sits on.
- There is never a second band. A global header stacked on a page toolbar says the same thing twice, and none of the nine boards stack. Filters fuse into whichever band already exists rather than earning a strip of their own.
- On an object route the band exists because the sidebar cannot express depth. That is its only job, which is why the lineage variant carries exactly three things: the way back, the lineage itself, and the one action that belongs to the object you are looking at.
- The toolbar variant is verbs fused with filters, in that order, left to right, at the control rung. Placement picks the size and this row is a toolbar, so everything standing in it is 32px, never 28px. Agent Focus may pass `edge="none"` so the band sits flush on the transcript: no bottom hairline and `bg-shell` (same fill as the work column) — never `bg-muted`, which left a colour seam.
- Command search is not band furniture. It moves to Cmd-K only: a field reached by shortcut should not park 320px of chrome on every page. A search that filters the rows below it is a page filter and belongs in the toolbar variant; a search that navigates the product is Cmd-K.
- Reading surfaces take no band at all. A masthead is content, not chrome, and an edition that wanted a toolbar wanted to be a queue instead.
- The band renders its own back affordance so every object route reaches back the same way; the caller supplies where back goes, never what it looks like.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
