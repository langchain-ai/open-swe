# ConfirmableAction

Required, not optional, for any action that permanently destroys something or sends on a rep's behalf: deleting a campaign, wiping account memory, releasing a queued send, revoking a rep's Gmail grant. If undo is impossible, the flow is...

Import: `@langchain/gtm-platform-design-system/patterns/confirmable-action`
Source: `src/patterns/confirmable-action.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ConfirmableAction

| Prop | Type | Required |
|---|---|---|
| `default` | `{` | yes |

## The decisions this carries

- Required, not optional, for any action that permanently destroys something or sends on a rep's behalf: deleting a campaign, wiping account memory, releasing a queued send, revoking a rep's Gmail grant. If undo is impossible, the flow is this pattern.
- A risk coloured Button on its own is never a destructive flow. The button is only the trigger; the decision lives in the dialog. A one click destroy is a bug, however small the object.
- The confirmation is one standard dialog: state the consequence in plain language, keep Cancel on screen, and name the final button for its effect rather than agreement.
- Never ask the user to type an email, name, title, or identifier to confirm. Repeating a label is ceremony, not authorization, and it trains people to move through destructive flows mechanically.
- Do not invent a second register at a call site. A hand-rolled arming boolean with its own Keep/Delete pair is this pattern, badly, and duplicated.
- The pattern owns its dialog one of two ways and never both. Pass `trigger` and it opens on press, which is the common case. Mount it with `onDismiss` when the host already knows which object is being acted on and there is no button left to press: a row menu that names a thread or a band that names the record on screen. Unmount it when dismissed. Either way the dialog and failure line are this pattern's.
- Cancel is always on screen, and Escape or a press outside close the dialog for free, right up until confirm is in flight. Once the promise is pending nothing closes the dialog, both actions are disabled, and the button shows progress in a slot it already occupied.
- A rejected confirm keeps the dialog open, states the reason inline on the risk role, and re-enables the actions, so the second attempt costs one click rather than a re-navigation. The reason is problemMessage with this pattern's fallback: never an HTTP status line, a capability title, or any other machine refusal.
- A decision may carry one `alternate`: a narrower version of the same act, outline, beside the confirm. Use it when the dialog would otherwise need a second trigger in the footer behind it for something the rep is deciding right now (send this email, or send it and queue the LinkedIn request). It is never a different object's action, never a third way to say no, and there is never more than one. Cancel remains the only way out.
- Destroy wears risk as an outline, never as a fill. CORE 14 has no filled risk surface, and a filled alarm colour on a 32px control is the loudest thing on the page for an action the user has already been warned about twice. Send-on-behalf uses the same dialog and a primary confirm: starting a campaign is irreversible, not destructive, and must not wear the delete colour.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
