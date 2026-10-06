# DiffRow

DiffRow shows one field mutation: caps label · before → after. The default is chips (muted line-through before, ink after). `layout="block"` is the same evidence for multiline markdown: stacked pre-wrapped mono blocks, never a second sid...

Import: `@langchain/gtm-platform-design-system/patterns/diff-row`
Source: `src/patterns/diff-row.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### DiffRow

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `before` | `ReactNode` | no |
| `after` | `ReactNode` | yes |
| `layout` | `"chip" \| "block"` | no |
| `className` | `string` | no |

### ChangeSetSection

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `children` | `ReactNode` | yes |
| `className` | `string` | no |

### ChangeSet

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | no |
| `children` | `ReactNode` | yes |
| `className` | `string` | no |

## The decisions this carries

- DiffRow shows one field mutation: caps label · before → after. The default is chips (muted line-through before, ink after). `layout="block"` is the same evidence for multiline markdown: stacked pre-wrapped mono blocks, never a second side-by-side editor. Empty before reads as '—'.
- ChangeSet has exactly two consumers and both are shipped: a play version compared to live (`VersionedEditor`'s `diff` slot) and a proposed or completed write (`Receipt`). Every before-and-after in this product is one of those two. When a third surface needs one, grow this pattern — never a second diff vocabulary.
- Compose many DiffRows into a ChangeSet — one quiet subcard (`border-line` + `rounded-control`), never a second Frame. Nesting Frame inside Receipt is how double mats come back.
- Related fields share a ChangeSetSection (caps subsection header over its rows). Flat ChangeSets stay flat; do not invent a section for a single orphan row.
- Use mono for values (identifiers, amounts, stages). Never sparklines, KPI tiles, or an action lane — the host owns the verb, and the two hosts own different ones: the Receipt's is already done, the PublishGate's is Publish. DiffRow is evidence of what changed, never where you change it.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
