# FormField

This is the explicit submit family: creation flows, invite flows, wizard steps. Nothing has happened until the user presses the button. If the surface is configuration, a set of independent switches a user comes back to and adjusts, it i...

Import: `@langchain/gtm-platform-design-system/patterns/form-field`
Source: `src/patterns/form-field.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### FormField

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `control` | `ReactElement<Record<string, unknown>>` | yes |
| `help` | `string` | no |
| `error` | `string` | no |
| `required` | `boolean` | no |

### FormSection

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `titleId` | `string` | no |
| `description` | `string` | no |
| `descriptionId` | `string` | no |
| `children` | `ReactNode` | yes |

## The decisions this carries

- This is the explicit submit family: creation flows, invite flows, wizard steps. Nothing has happened until the user presses the button. If the surface is configuration, a set of independent switches a user comes back to and adjusts, it is not a form, it is SettingSection, and it saves on change with no button at all. Pick the family first; the two are not interchangeable fills of one pattern.
- VALIDATION DISPLAY, ADOPTED DEFAULT, PENDING AMAL'S CONFIRMATION: a validation message renders inline, under the control that is wrong, on blur or on submit. Never on every keystroke, because a field the user has not finished typing is not yet wrong; and never as a toast, because a toast puts the complaint somewhere other than the thing being complained about, and it expires while the field does not.
- The error line lives under the CONTROL, not under the label and not at the top of the form. It keeps the message aligned to the thing that is wrong, and it keeps the label from reflowing when the message appears.
- An error is never only a colour. The line carries a glyph and words on the risk role, the control goes aria-invalid, and aria-describedby points at the message, so the failure survives a screen reader and a monochrome screen alike. This pattern wires all three itself: a call site cannot forget.
- Help text is the field's permanent explanation and the error is a temporary complaint, so both can be on screen at once and both are described to the control. Do not use the help line as an error slot that changes colour.
- A required field is marked once, beside the label, and the marker is decorative: the control carries aria-required and nothing else. Native browser validation is off by design, because its bubbles are a second validation display and the rule above says there is one.
- One primary action per form, named for its effect. 'Save policy', 'Send invites', 'Create campaign'. Never 'Submit', never 'OK'. The name is the last thing a user reads before committing and it should say what will happen.
- That one action belongs to the FORM, not to a section, which is why FormSection has no actions slot. A form with an action row per section is a form that will grow three primary buttons, and the user will not know which one finishes the job.
- FormSection groups fields at ONE gap under one heading. The heading and its description are one pair at `xs`. That pair sits one `md` step above the first field. A label sits one `sm` step above its control. Sibling sections stack at one `2xl` step, only through FormStack or SECTION_STACK_GAP. A wizard pane uses that same gap. Do not pass a local gap around sections. Native fieldset and legend chrome is reset so the title is the same size as a field label, not a leftover heading. No nested groups, no per field spacing overrides: an even rhythm is what makes a long form scannable, and a section that needs its own rhythm is a second FormSection, not a denser group.
- A form spends two sizes: text-label and text-meta. The section title and the field label share LABEL_CLASS from ui/label. The title text lives in a span inside the legend, because a native legend ignores font on the element itself. Description and help share HELP_CLASS. An error stays text-label and shifts to the risk role; state is colour, not a third size. Typed values use FIELD_VALUE_CLASS from Input: 16px below md so Safari does not zoom, then the label size. text-page belongs to PageFrame, not to a dialog or a fieldset. ReUI form-2/4/7 and the settings blocks stay in two rungs the same way.
- No tabbed forms, rejected from the ReUI survey. Seven tabs over one form with one footer means a user can leave an unsaved, invalid field behind a tab they cannot see. A form that is too long for a column is a wizard with steps, which is honest about the same thing.
- Destructive submits still compose ConfirmableAction. A primary button named for its effect explains the effect; it does not authorise an irreversible one.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
