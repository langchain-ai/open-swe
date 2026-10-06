# MetricCard

Dashboard headline metrics are cards with an icon, a clear title, one dominant value, and one context line. Dense record metadata remains a StatReadout row or cell.

Import: `@langchain/gtm-platform-design-system/patterns/metric-card`
Source: `src/patterns/metric-card.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### MetricCard

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `value` | `string \| number` | yes |
| `detail` | `string` | no |
| `icon` | `Glyph` | yes |
| `info` | `ReactNode` | no |
| `sparkline` | `ReactNode` | no |
| `onDrill` | `() => void` | no |
| `drillLabel` | `string` | no |
| `onOpen` | `() => void` | no |
| `openLabel` | `string` | no |
| `emphasis` | `"title" \| "display"` | no |

### MetricCardGrid

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `columns` | `keyof typeof GRID_COLUMNS_CLASS` | no |
| `children` | `ReactNode` | yes |

## The decisions this carries

- Dashboard headline metrics are cards with an icon, a clear title, one dominant value, and one context line. Dense record metadata remains a StatReadout row or cell.
- A metric card grid is one ReUI Frame containing repeated FramePanel cards. Pass `columns` for the peak count (two, three, or four; four is the default). It reflows from one column up to that count instead of shrinking a strip. Pick the step that divides the set: a row of counts that does not fill its peak leaves a card stranded on its own line. That is why the three step has no two-column rung: two across strands the third at every width between the rungs, so a set of three goes from one column straight to three.
- The icon and title share the first band. The title names the metric, so the icon is decorative and never repeats the accessible name.
- Three bands, two spaces: the title band, the value, the context line. The title band sits a `md` step above the value and the context line a `sm` step under it, both on the spacing scale and both owned here, so every card in the product breathes the same way whatever grid it sits in.
- The value is preformatted, mono, tabular, and the card's one focal point. The pattern never rounds, abbreviates, adds units, or infers whether a change is good or bad.
- The context line names the window, coverage, or certainty. Omit it when that context lives in the info disclosure instead. When present it wraps to a second line and clamps there, so context is never silently clipped and card heights stay bounded.
- Metric cards are readings, not controls. They have no hover action, menu, link, or decorative trend badge unless a real owning route and comparison contract exist.
- The optional info slot is one of three sanctioned affordances: a small trigger at the end of the title band opening the metric's provenance (description, coverage note, window, denominator, reproduce path). It discloses; it never navigates.
- The optional sparkline slot is word-sized, sits on the value's line at the card's trailing edge, and draws the same stored series the value summarizes, so the comparison contract is real. Trailing rather than hugging the value, so the figure reads first and alone and the shape is a second glance rather than part of the number. A sparkline from any other series, or a decorative one, is the trend badge rule 6 forbids.
- Given onDrill, the whole card is the other sanctioned affordance: it zooms into the series its own sparkline draws, and nothing else. Whole card rather than sparkline alone, because a 96px target reads as decoration and a card that responds only in one corner teaches nothing; the cursor and the hover lift are what say it is clickable at all. It satisfies rule 6 through that comparison contract rather than despite it. Without onDrill the card stays inert, and a card with no sparkline never becomes clickable, since there would be nothing to zoom into.
- Given onOpen on a card with no sparkline, the whole card is the third sanctioned affordance: it opens the collection its value counts (the list of those domains, reps, or mailboxes), in a sheet over the page. A trailing ChevronRight at the end of the title band says so, because without a sparkline nothing else on the card reads as a way in. `openLabel` names the collection, not the gesture. A card with a sparkline drills into its series instead and never also opens; onOpen on it is inert.
- The drill and the open are each a real button covering the card, never a role on the panel. A button role on the panel would swallow the info trigger inside it: assistive technology does not expose interactive descendants of a button, so the provenance disclosure would disappear from the accessibility tree even though it still works with a mouse. The covering button and the info trigger are siblings instead, both separately focusable, and the panel carries the hover and focus styling for whichever of them the reader is on.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
