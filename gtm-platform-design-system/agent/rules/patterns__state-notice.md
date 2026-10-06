# StateNotice

This is the one shape for a thing that did not happen: a send the door refused, a job that stopped, a connection that lapsed, an import that found nothing to do. Never invent a second one per surface.

Import: `@langchain/gtm-platform-design-system/patterns/state-notice`
Source: `src/patterns/state-notice.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### StateNotice

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `description` | `string` | yes |
| `icon` | `Glyph` | yes |
| `tone` | `StateNoticeTone` | yes |
| `action` | `ReactNode` | no |

## The decisions this carries

- This is the one shape for a thing that did not happen: a send the door refused, a job that stopped, a connection that lapsed, an import that found nothing to do. Never invent a second one per surface.
- The title names what happened in the user's own words. Never 'That did not go through', never 'Error', never a status code: somebody reading six of these in a week has to tell them apart at a glance.
- Tone is who clears it, not how bad it is. RISK is a wall the user cannot clear alone. ATTENTION is a person clearing it, named in the sentence. INFO is time clearing it with nobody acting.
- One mark, one title, one sentence, at most one action. The sentence never repeats the title, and it says what happened to their work: nothing was sent, the campaign is untouched, the draft stays here.
- Plain words. Say mailbox, not identity; on hold, not PAUSED; we could not, not the request was refused. No verdict names, no slugs, no provider text.
- It sits above the object it is about and leaves that object whole, buttons live. Every state here clears, and the user's next move is the same button.
- Never dismissible. A notice waved away is one the user meets again at the same button with no memory of why.
- Use `Alert` instead for a message that is not about a blocked object: a tip, a confirmation, a page-level announcement.
- CopyGrammar: Object · State · Evidence · Action. The title is the state, the sentence is the evidence, the button is the action.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
