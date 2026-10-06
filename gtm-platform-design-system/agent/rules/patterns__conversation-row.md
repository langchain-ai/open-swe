# ConversationRow

An inbox row is not a QueueRow. It is a conversation rung with a 32px mark lane, contact over account, and a trailing column of meta over the status chip — never meta inside the identity and badge floating mid-row.

Import: `@langchain/gtm-platform-design-system/patterns/conversation-row`
Source: `src/patterns/conversation-row.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ConversationRow

| Prop | Type | Required |
|---|---|---|
| `id` | `string` | yes |
| `contact` | `string` | yes |
| `account` | `string` | no |
| `accountKind` | `"account" \| "activity"` | no |
| `meta` | `string` | no |
| `statusBadge` | `ReactNode` | no |
| `provider` | `ProviderId` | no |
| `unread` | `boolean` | no |
| `selected` | `boolean` | no |
| `onOpen` | `() => void` | no |

## The decisions this carries

- An inbox row is not a QueueRow. It is a conversation rung with a 32px mark lane, contact over account, and a trailing column of meta over the status chip — never meta inside the identity and badge floating mid-row.
- Two type rungs only: `text-label` for contact, `text-meta` for the second line and time. Flat list: second line is the account, with Building2 so the company is a mark and not a caption. Under an account group: second line is the mailbox when the title is a person, otherwise activity (`signalLine`). The building stays on the section. No preview third line. No `leading-*` overrides — the type pair owns the line height.
- Unread is weight plus a primary corner badge on the ProviderMark (`size-1.5`, top-right of the mark lane, canvas ring so it reads on the well) — never a left gutter, never a second trailing clock-dot, and never colour alone.
- The row is click-to-open. There is no overflow menu, no hover-revealed action, and no reserved action lane. Time and status stay put. Edit lives on the work-pane ApprovalArtifact.
- Statuses are the locked four: NEEDS_YOU, SCHEDULED, WAITING, CLOSED. Machine tokens stay in data; chip copy is always the sentence-case label from `CONVERSATION_STATUS_LABEL` (Needs you / Scheduled / Waiting / Closed) — never underscored enum text. Brand pipeline states belong on send-pipeline objects, not this row.
- Status chips are filled `Badge tier="quiet"` via `ConversationStatusBadge` — tinted wash + tone ink + the status glyph. Never `notable` outline pills on this row; outline is for one-off emphasis elsewhere, not the inbox status lane.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
