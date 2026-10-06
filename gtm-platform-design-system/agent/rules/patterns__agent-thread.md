# AgentThread

The thread measure is 704px — a documented contentWidth exception, not a fifth rung (`APP_SHELL_RULES`). Centre it in the work pane. The number lives in one place, `--gtm-container-thread`, reached as `max-w-(--container-thread)`: an exc...

Import: `@langchain/gtm-platform-design-system/patterns/agent-thread`
Source: `src/patterns/agent-thread.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### AgentThread

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `className` | `string` | no |

### AgentThreadDivider

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |

### AgentInterpretation

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `className` | `string` | no |

### AgentTurn

| Prop | Type | Required |
|---|---|---|
| `name` | `string` | no |
| `children` | `ReactNode` | yes |

### UserTurnCopy

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | no |
| `text` | `string` | yes |
| `displayText` | `string` | no |

### UserTurnMessageDialog

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `onOpenChange` | `(open: boolean) => void` | yes |
| `open` | `boolean` | yes |
| `text` | `string` | yes |

### UserTurn

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `copyText` | `string` | no |

## The decisions this carries

- The thread measure is 704px — a documented contentWidth exception, not a fifth rung (`APP_SHELL_RULES`). Centre it in the work pane. The number lives in one place, `--gtm-container-thread`, reached as `max-w-(--container-thread)`: an exception is named once, never retyped as an arbitrary value.
- User turns are right-aligned bubbles. Agent turns use the full content width, without an avatar or mirrored bubble.
- The interpretation block is a single muted surface with one pad (`px-3 py-2.5`) and an asymmetric radius (speech-tail on the bottom-leading corner). It is the only asymmetric radius in the product.
- Do not compose bubbles from Frame. Frame is mat + panel — two pads — and that is why interpretation looked double-inset. Frame belongs to artifacts (ApprovalArtifact), not speech.
- Day dividers are centred meta text with no rule. Tool groups and ApprovalArtifacts compose inside agent turns; they are not this pattern's job.
- Long user copy stays speech. Estimate more than sixteen visual lines (a newline or a wrap at the bubble measure) and clamp with `line-clamp-16`. View more opens a dialog for the rest, with Copy. Mentions and real file chips stay outside the clamp. Do not expand in the bubble, and do not mint a fake attachment.
- User turns keep hover/focus Copy. The transcript shows assistant Copy only on its last prose block, after its tools, resources, receipts, and structured view, once streaming ends. `TurnCopyButton` copies the source string and swaps to Check. Intermediate assistant blocks have no Copy. Code fences keep their own copy.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
