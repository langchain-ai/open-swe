# GroupRow

A group row is a row in the same grid, not a heading outside it. The anatomy is fixed: disclosure chevron, then the group identity, then the compact count.

Import: `@langchain/gtm-platform-design-system/ui/group-row`
Source: `src/ui/group-row.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### GroupRowContent

| Prop | Type | Required |
|---|---|---|
| `count` | `number` | yes |
| `depth` | `number` | no |
| `expanded` | `boolean` | yes |
| `label` | `ReactNode` | yes |
| `onToggle` | `() => void` | yes |
| `compact` | `boolean` | no |

## The decisions this carries

- A group row is a row in the same grid, not a heading outside it. The anatomy is fixed: disclosure chevron, then the group identity, then the compact count.
- The identity is that field's SchemaCell when the surface provides one. A bare string still becomes one Quiet badge. Do not set the group name in font-medium body type.
- The count sits immediately after the identity. It is mono tabular meta, not a badge, and it does not travel to the trailing edge of the first column. A wide pinned name column is not a reason to stretch the label.
- Nested groups indent the chevron. The count stays on the identity cluster at every depth.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
