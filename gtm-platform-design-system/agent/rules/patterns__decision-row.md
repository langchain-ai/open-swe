# DecisionRow

Home is a router. The row's only verb is Open — never Approve, Send, or Snooze on the Home queue (platform decision 1.2). Owning surfaces hold the decision UX.

Import: `@langchain/gtm-platform-design-system/patterns/decision-row`
Source: `src/patterns/decision-row.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### DecisionList

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `children` | `ReactNode` | yes |

### DecisionRow

| Prop | Type | Required |
|---|---|---|
| `id` | `string` | yes |
| `object` | `string` | yes |
| `reason` | `string` | yes |
| `meta` | `string` | no |
| `badge` | `ReactNode` | no |
| `provider` | `ProviderId` | no |
| `unread` | `boolean` | no |
| `selected` | `boolean` | no |
| `href` | `string` | no |
| `onOpen` | `() => void` | no |

## The decisions this carries

- Home is a router. The row's only verb is Open — never Approve, Send, or Snooze on the Home queue (platform decision 1.2). Owning surfaces hold the decision UX.
- Mount rows inside `DecisionList`, never straight into a Stack. The owner is a named `ul`, every row is an `li`, and route-only rows are real links so open-in-new-tab, copy-link, and browser status work without JavaScript.
- Geometry is the conversation rung: 48px minimum, source mark lane, identity stack, trailing column of state over time. The badge never shares the title baseline. There is no action lane and no 165px primary cluster from the Home board — that board violates one-primary and is dead.
- Every item carries the agent's one-line ranking reason as the secondary line, visible without expanding (decision 1.4). Fuller reasoning is one hop away in the owning surface.
- CopyGrammar field order: Object · State · Evidence in the identity; never 'I found something interesting!' Rank is position and weight, never a pill.
- Unread is weight plus an optional dot, never colour alone. Nothing animates.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
