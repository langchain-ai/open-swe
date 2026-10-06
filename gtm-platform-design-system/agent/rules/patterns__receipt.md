# Receipt

Receipt is the terminal step of CopyGrammar: Object → State → Evidence → Action → Receipt. Render inline under the action and keep it inspectable.

Import: `@langchain/gtm-platform-design-system/patterns/receipt`
Source: `src/patterns/receipt.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### Receipt

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `titleClassName` | `string` | no |
| `summary` | `string` | no |
| `heroValue` | `string` | no |
| `heroLabel` | `string` | no |
| `children` | `ReactNode` | no |
| `mark` | `ReactNode` | no |
| `meta` | `ReactNode` | no |
| `className` | `string` | no |
| `spacing` | `"sm" \| "default" \| "lg"` | no |

## The decisions this carries

- Receipt is the terminal step of CopyGrammar: Object → State → Evidence → Action → Receipt. Render inline under the action and keep it inspectable.
- Shell is ReUI `Frame` stacked+dense — chrome header, optional figure panel, body panel. Same multi-panel family as ApprovalArtifact and VersionedEditor. Never a single stuffed panel with figure and diffs competing for one pad.
- A receipt may lead with one 24px IconWell or ProviderMark when the surrounding surface does not already name the result family. The mark stays in the chrome beside the title; never spend a second panel only to repeat the provider or object kind.
- Type order is title → summary → figure → changes. Standalone receipts use `text-title`; conversation adapters lower the title through their shared `text-label` object-title class. Figure and after values use `text-label`; summary, labels, and before values use `text-meta`.
- A dedicated figure block is legal only on Receipt. It is label-scale mono (`text-label`), same value type as StatReadout — Receipt presents the number; it does not invent a display rung.
- Field mutations mount as ChangeSet / ChangeSetSection / DiffRow in the body panel — one quiet subcard, optional subsections. Do not bury diffs in prose and do not wrap them in a second Frame.
- Keep the shell quiet: no Urgent accents, no second primary CTA. A Receipt is confirmation, not a new decision.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
