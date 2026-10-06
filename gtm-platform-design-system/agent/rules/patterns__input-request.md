# InputRequest

Use InputRequest only to collect missing information. ApprovalArtifact remains the boundary for a consequential send or write.

Import: `@langchain/gtm-platform-design-system/patterns/input-request`
Source: `src/patterns/input-request.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### InputQuestionField

| Prop | Type | Required |
|---|---|---|
| `question` | `InputRequestQuestion` | yes |
| `value` | `InputRequestAnswer` | no |
| `error` | `string` | no |
| `onValueChange` | `(value: InputRequestAnswer) => void` | yes |
| `onUploadFiles` | `(files: readonly File[]) => Promise<readonly string[]>` | no |
| `onUploadStateChange` | `(questionId: string, uploading: boolean) => void` | no |

### InputRequestComposer

| Prop | Type | Required |
|---|---|---|
| `request` | `InputRequestSpec` | yes |
| `answers` | `InputRequestAnswers` | yes |
| `onAnswerChange` | `(questionId: string, value: InputRequestAnswer) => void` | yes |
| `onSubmit` | `(answers: InputRequestAnswers) => Promise<void>` | yes |
| `onSaveProgress` | `(answers: InputRequestAnswers) => Promise<void>` | no |
| `onCancel` | `() => void` | no |
| `onAskQuestion` | `(answers: InputRequestAnswers) => void` | no |
| `step` | `number` | no |
| `onStepChange` | `(step: number) => void` | no |
| `submitting` | `boolean` | no |
| `completeLabel` | `string` | no |
| `displayTitle` | `string` | no |
| `onUploadFiles` | `(files: readonly File[]) => Promise<readonly string[]>` | no |

### InputRequestReceipt

| Prop | Type | Required |
|---|---|---|
| `state` | `InputRequestReceiptState` | yes |
| `title` | `string` | no |
| `summary` | `string` | no |
| `answeredCount` | `number` | no |
| `answers` | `readonly InputRequestReceiptAnswer[]` | no |
| `review` | `InputRequestReceiptReview` | no |

## The decisions this carries

- Use InputRequest only to collect missing information. ApprovalArtifact remains the boundary for a consequential send or write.
- The Agent supplies semantic sections, stable question IDs, closed question types, labels, help, bounded options and editable prefills. It never supplies React props, component names, layout, HTML, URLs, callbacks, ownership or run tokens.
- Every pending web request extends the composer. It never opens a modal or creates a second interaction surface while answers are being collected.
- A pending request opens in layout flow so the transcript keeps scrolling, and can collapse to one Needs your input row without losing answers, progress or the blocking state.
- The same semantic request maps to native web and Slack controls. Web never renders Block Kit, Slack never translates React, and neither adapter changes the stored question or option IDs.
- The composer extension keeps one current question group dominant. Progress is one quiet count and one line in every viewport; a permanent section rail is too much navigation for a temporary prerequisite.
- Long option sets become searchable inside the field. Search filters labels only and never changes stable option IDs. Option labels wrap in full and never truncate, and the list scrolls with the step body rather than as a second nested region. Multi-select limits stay visible and are enforced before submission; at the limit the field says no more can be selected instead of silently disabling the rest.
- Prefills remain editable and show bounded provenance. They are suggestions from a named source, never hidden defaults.
- Validation appears under the field on Continue or the effect-named final action. It is words plus a glyph plus ARIA wiring, never colour alone and never a toast.
- Continue validates and saves the current step before advancing. Cancel stays visible as a secondary action. The final action names what the Agent will do next, such as Generate POV execution doc. Help sits between the question label and its control, never after it.
- Ask a question saves the draft, sets the form aside as one compact pending row with a Resume action, and returns the chat prompt. The request stays pending with its answers and current section; a question is an ordinary chat turn, never a cancel or a second request.
- A submitted request uses InputRequestSubmission inside the existing UserTurn bubble after the continuation message supplies its transcript anchor. Its form icon, title, Submitted status, sections, question labels and answer values stay visibly structured and read-only. Completed forms use tight spacing and two columns for short fields when their own container is wide enough; long text and uploads span both columns. The bubble owns containment and copy; never nest another card or render filled answers as editable inputs. Cancelled and expired requests close without leaving an unanchored card at the bottom of the chat.
- Submitted uploads render as file pills when the host supplies an authenticated file destination. The host builds that destination from the owned thread and filename; the request never supplies a URL.
- Unknown schema versions or question types fail closed. Show a recoverable unsupported receipt and ask the user to start a fresh request instead of dropping fields.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
