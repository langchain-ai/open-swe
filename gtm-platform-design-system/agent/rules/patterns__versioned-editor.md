# VersionedEditor

VersionedEditor is the third save family. Prompt changes stay browser-local until Save draft updates the one mutable team draft or Publish creates the immutable version. Local, Saving draft, Draft saved, Publishing, and Publish failed be...

Import: `@langchain/gtm-platform-design-system/patterns/versioned-editor`
Source: `src/patterns/versioned-editor.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### VersionedEditor

| Prop | Type | Required |
|---|---|---|
| `versionLabel` | `string` | no |
| `leading` | `ReactNode` | no |
| `draftBadge` | `ReactNode` | no |
| `toolbar` | `ReactNode` | no |
| `children` | `ReactNode` | yes |
| `className` | `string` | no |
| `collapsed` | `boolean` | no |
| `onChromeToggle` | `() => void` | no |
| `diff` | `ReactNode` | no |
| `gate` | `ReactNode` | no |

### PublishGate

| Prop | Type | Required |
|---|---|---|
| `versionLabel` | `string` | yes |
| `summary` | `string` | no |
| `onPublish` | `() => void` | no |
| `disabled` | `boolean` | no |
| `loading` | `boolean` | no |
| `onDiscard` | `() => void \| Promise<void>` | no |
| `dryRun` | `ReactNode` | no |
| `secondary` | `ReactNode` | no |
| `className` | `string` | no |

## The decisions this carries

- VersionedEditor is the third save family. Prompt changes stay browser-local until Save draft updates the one mutable team draft or Publish creates the immutable version. Local, Saving draft, Draft saved, Publishing, and Publish failed belong beside the actions, not in a toast. There is no page-level dirty bar.
- Publish is consequential but NOT destructive. It names its consequence in the summary and again in the button ('Publish v3'). It does not need a confirmation dialog.
- Discard draft IS destructive, so it goes through `ConfirmableAction`. Publish and Discard share a footer and must never swap grammars: confirm the one that destroys, not the one that ships.
- Rollback is republish, never delete. Returning to v2 publishes v2 again as the newest live pointer; no version is ever removed and this family has no Delete verb. History that can be erased cannot be evidence.
- Show version label + draft badge with existing Badge tiers — never invent a fourth draft colour.
- A version diff is `ChangeSet` / `DiffRow` in the `diff` slot — the same evidence object a Receipt uses for proposed writes (`DIFF_ROW_RULES`). Never a bespoke side-by-side editor, never a second diff vocabulary.
- `dryRun` is a reserved slot in the gate footer, held for the dry-run fast-follow. Empty until it lands; when it lands it is a Quiet/outline action left of Discard — never a second primary, and never a step Publish is blocked on.
- Follow the conversation draft anatomy: a quiet header on the Frame mat, one inset content panel, and one persistent trailing action footer. Do not duplicate actions in the header and footer.
- Compose under RecordHeader. Provisional StatReadout may sit beside the gate once — not a KPI strip.
- A collapsed preview is this same card with a shorter body, never a second chrome. The mask fades the cut so the panel fill stays honest.
- The chrome row is the expand control. There is no extra chevron. Interactive children keep their own clicks. Collapse interpolates a grid track from minmax(6rem, 0fr) to minmax(6rem, 1fr) at duration-fast ease-out-quint. Keep the inner content size stable so both directions stay smooth. Reduced motion is a cut.
- When the surface owns versions, the header leading slot is the version picker. Do not also print the same label as FrameTitle. The footer shows Edit and one Publish action while reviewing, replaced by Discard changes and Save draft while editing. Discard changes only restores the saved content; it never deletes a server draft. Share and Retire change the whole record and sit in RecordHeader actions. Compare, Copy, and Download live under a three-dots menu.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
