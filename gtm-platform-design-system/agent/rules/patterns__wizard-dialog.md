# WizardDialog

Use one Dialog, one scroll body, and one footer. A branch changes the current step; it never opens a second dialog.

Import: `@langchain/gtm-platform-design-system/patterns/wizard-dialog`
Source: `src/patterns/wizard-dialog.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### WizardDialog

| Prop | Type | Required |
|---|---|---|
| `open` | `boolean` | yes |
| `onOpenChange` | `(open: boolean) => void` | yes |
| `title` | `string` | yes |
| `description` | `string` | yes |
| `steps` | `readonly WizardStep[]` | yes |
| `step` | `number` | yes |
| `onStepChange` | `(step: number) => void` | yes |
| `onSubmit` | `() => Promise<void>` | yes |
| `validateStep` | `(step: number) => boolean` | no |
| `canContinue` | `boolean` | yes |
| `submitting` | `boolean` | yes |
| `completeLabel` | `string` | yes |
| `layout` | `WizardLayout` | no |
| `progressLabel` | `string` | no |
| `cancelAction` | `WizardCancelAction` | no |
| `secondaryAction` | `WizardSecondaryAction` | no |
| `onDiscard` | `() => void` | no |
| `recover` | `{` | no |
| `label` | `string` | yes |
| `onRecover` | `() => void` | yes |
| `children` | `ReactNode` | yes |

## The decisions this carries

- Use one Dialog, one scroll body, and one footer. A branch changes the current step; it never opens a second dialog.
- Compact is for one to four steps. Guided is for five to eight sections: a stable desktop rail yields to the current step body, while narrow screens keep Step N of M above the body.
- The active step is announced and future steps stay unavailable until the current step validates. Completed steps may be revisited.
- The primary button names its effect: Continue while moving, then Create campaign or Create play. Never Submit or OK. After a write lands and a later step fails, the primary becomes Open campaign or Open play.
- Keys, source types, schema names, and other wire language never appear. Derived identifiers stay behind the form.
- The desktop trail is one line per step: number and name. Descriptions live in the step body. Space sits between steps; the trail never packs two lines into a control-height button.
- At narrow widths the trail becomes Step N of M plus the current name. It never scrolls horizontally.
- Step and branch changes present immediately, including keyboard-submitted forms. Direction is semantic state, not a reason to animate frequent input.
- A last-step fork uses ChoiceCards. Each card opens one section. Returning to the cards is Back. Do not stack Link existing and Create new in one view.
- The wizard is a form, so it spends text-label and text-meta only. It never borrows text-page. Sibling sections use SECTION_STACK_GAP. Do not restyle the step or branch gap at a call site.
- A long guided form may offer Save and close as one secondary action. Back remains navigation, Continue remains the primary, and Cancel remains a terminal host action rather than a synonym for closing.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
