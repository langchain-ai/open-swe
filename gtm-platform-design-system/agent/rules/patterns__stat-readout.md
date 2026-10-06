# StatReadout

StatReadout is the dense number pattern for record chrome, metadata columns, and compact run summaries. Six settled boards put those values in a label/value pair at 28, 40, or 66px.

Import: `@langchain/gtm-platform-design-system/patterns/stat-readout`
Source: `src/patterns/stat-readout.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### StatReadout

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `value` | `string \| number \| null` | no |
| `shape` | `StatReadoutShape` | no |
| `tone` | `StatTone` | no |
| `delta` | `StatDelta` | no |
| `provisional` | `boolean \| string` | no |

### StatReadoutGrid

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `columns` | `keyof typeof GRID_COLUMNS_CLASS` | no |
| `children` | `ReactNode` | yes |

## The decisions this carries

- StatReadout is the dense number pattern for record chrome, metadata columns, and compact run summaries. Six settled boards put those values in a label/value pair at 28, 40, or 66px.
- Dashboard headline KPIs use MetricCard instead: an icon-led card with a clear title, dominant value, and context line. Do not stretch StatReadout into an analytics strip or add a fourth readout shape.
- The value is 13px, weight 500, IBM Plex Mono, tabular. Mono is not decoration here: it is what makes a column of numbers stack, and tabular figures are what stop a value jittering as digits change under a poll. The chart tooltip uses the identical type for the identical reason.
- The label is the meta rung in subtle ink, and it is a noun, not a sentence. 'Open pipeline', not 'How much pipeline is open'. The pair is read as a unit, so a label long enough to wrap has already lost the shape.
- Semantic tone colours the value and nothing else. Not the label, not the cell, not a background. Tone is a claim that this number is good or bad; a tinted cell makes that claim about the whole readout, and a tinted label makes it about the metric rather than the reading.
- Empty is an em-dash, in the strong hairline ink. Never a zero, never 'N/A', never a blank: zero is a fact and absence is not, and a rep acting on a fabricated zero is the failure this rule exists to prevent.
- A delta carries a sign glyph as well as a tone. Colour alone fails for anyone who cannot separate the two hues, and it fails again in a screenshot pasted into Slack. The direction glyph is the affordance; the tone is the emphasis on top of it.
- Direction is not sentiment. Rising churn is `risk` and falling spend may be `positive`, so the delta takes its tone from the caller and never derives it from the arrow. A component that guesses will be wrong on exactly the metrics that matter.
- Values arrive formatted. The component never rounds, abbreviates, or adds a unit, because three surfaces will otherwise invent three million-abbreviations. Formatting belongs to the data layer, next to the thing that knows what the number means.
- Shape is placement, not preference. `signal` is the 28px rail row, `field` is the 40px key/value row with its hairline, `cell` is the 66px grid tile inside `StatReadoutGrid`. There is no fourth shape and no size prop; a surface that wants a bigger number wants a different pattern.
- Provisional is a claim about certainty, not absence. The em-dash means the value is missing; `provisional` means the value is present but not yet certified (Campaign Studio rates before funnel parity). It renders once as a `text-meta` sentence-case note beside the value — never an 11px footnote, never a caps kicker, never a fourth badge tier. (`surface-decisions` + annex 06.)
- Nothing here animates. Not the value on change, not the delta on mount. A number that counts up is a number a rep cannot read yet, and these readouts sit on surfaces that poll.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
