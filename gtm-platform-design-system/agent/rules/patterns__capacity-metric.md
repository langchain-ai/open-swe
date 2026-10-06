# CapacityMetric

Capacity is a remaining allowance, not usage completed. The dominant number says what is left and the meter fills in the same direction.

Import: `@langchain/gtm-platform-design-system/patterns/capacity-metric`
Source: `src/patterns/capacity-metric.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### CapacityMeter

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `remaining` | `number` | yes |
| `limit` | `number` | yes |
| `tone` | `CapacityTone` | no |

### CapacityMetric

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `icon` | `Glyph` | yes |
| `remaining` | `number` | no |
| `limit` | `number` | no |
| `meterLabel` | `string` | no |
| `detail` | `ReactNode` | no |
| `unavailable` | `boolean` | no |
| `tone` | `CapacityTone` | no |

### CapacityMetricGrid

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `children` | `ReactNode` | yes |
| `columns` | `1 \| 2` | no |

## The decisions this carries

- Capacity is a remaining allowance, not usage completed. The dominant number says what is left and the meter fills in the same direction.
- A capacity set is one ReUI Frame with one hairline outline, no mat gutter or nested panel border, and repeated metric cells. Each cell has one icon, one clear title, one available count, and one segmented meter.
- The caller owns every label and number. The pattern clamps the meter for presentation but never derives a provider limit, reset window, or business meaning.
- A zero balance is a fact and uses the risk tone. Missing or unreadable data says Unavailable and never renders as zero.
- Numeric limits and secondary windows belong in the containing surface's help. A next-available time may sit below the meter when capacity is zero.
- Capacity metrics are readings, not controls. Navigation, help, refresh, and mutation affordances belong to the containing product surface.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
