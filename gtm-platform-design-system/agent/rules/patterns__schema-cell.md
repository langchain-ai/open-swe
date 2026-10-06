# SchemaCell

A cell has a schema type, and the type picks the chrome. Surfaces do not restyle the same field two ways.

Import: `@langchain/gtm-platform-design-system/patterns/schema-cell`
Source: `src/patterns/schema-cell.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## The decisions this carries

- A cell has a schema type, and the type picks the chrome. Surfaces do not restyle the same field two ways.
- Wire vocabulary stops at the adapter. A visible enum uses an explicit product label, with `humanizeToken` only as a bounded fallback; never render underscores, provider identifiers, or storage names. A metric label states whether the value is an amount, count, rate, or share so the user never has to infer the unit from formatting.
- Single-select (`enum`) is one Quiet badge. Tone may vary with the value; the geometry may not. A value glyph may sit in the badge; it does not replace the label. The badge keeps the start of the label and truncates the end inside the column rather than wrapping, overflowing, or clipping both sides. A group row identity uses this same enum grammar; `GroupRowContent` owns the chevron and the hugging count.
- Multi-select (`set`) is compact Quiet badges in one nowrap row. As many values as fit stay visible; the last visible value may truncate. Further values collapse to a +N remainder chip. Widening the column reveals more chips. Hover or click the cell to read every value as the same Quiet badges, wrapping if a name is long. A wrapping paragraph of names is not a cell.
- Text truncates. Money and counts are mono tabular. A date is the calendar day the upstream published, also mono. A link is compact, https only, and stops the row click. An external platform hop carries that platform's branded logo, never a generic external-link glyph.
- Absence is the reserved empty mark, never a zero, never N/A, never a blank.
- Do not duplicate one fact as a number, badge, and progress bar in the same cell or preview. Choose the reading that best supports the decision, and reserve a second encoding for a genuinely different comparison.
- Every column header carries a representative glyph plus the title. The glyph is decorative; the accessible name is the title.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
