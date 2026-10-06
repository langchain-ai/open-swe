# QueueRow

Lanes are fixed-width so the columns line up down the queue: 16px checkbox, 10px state dot, the flexible identity, 110px badge, 100px meta, 28px action. A lane that sizes itself to its contents makes every row a different shape, and a re...

Import: `@langchain/gtm-platform-design-system/patterns/queue-row`
Source: `src/patterns/queue-row.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### QueueRow

| Prop | Type | Required |
|---|---|---|
| `primary` | `string` | yes |
| `secondary` | `string` | no |
| `summary` | `string` | no |
| `density` | `QueueRowDensity` | no |
| `unread` | `boolean` | no |
| `selected` | `boolean` | no |
| `onSelect` | `() => void` | no |
| `selectable` | `boolean` | no |
| `checked` | `boolean` | no |
| `onCheckedChange` | `(checked: boolean) => void` | no |
| `checkboxLabel` | `string` | no |
| `state` | `ReactNode` | no |
| `meta` | `string` | no |
| `action` | `ReactNode` | no |

### QueueList

| Prop | Type | Required |
|---|---|---|
| `label` | `string` | yes |
| `multiSelect` | `boolean` | no |
| `children` | `ReactNode` | yes |

## The decisions this carries

- Lanes are fixed-width so the columns line up down the queue: 16px checkbox, 10px state dot, the flexible identity, 110px badge, 100px meta, 28px action. A lane that sizes itself to its contents makes every row a different shape, and a rep scanning forty rows is reading columns, not rows.
- Reserve the lane, fill it conditionally. The state and action lanes are mounted on every row whether or not that row has a dot or an action, because a list that reflows as items are read or hovered is a list nobody can aim at.
- One action, revealed on hover and on focus, and it is an icon button. The second verb is not a second button: it belongs to the row's own affordances (the detail pane it opens, the row menu, the keyboard). Two trailing buttons is the moment a queue turns into a toolbar per row.
- A hover-revealed action must have a non-hover path, or it is not an action. There is no hover on a touch-primary device, so the row asks `useTouchPrimary` and simply keeps the action visible there; the lane was already reserved, so nothing moves and nothing is added. Any affordance anywhere that only appears on `:hover` owes the same answer.
- A queue is rows, never cards. No panel, no radius, no shadow per item; separation is the one hairline underneath. Boxing each row spends the containment signal on the thing that needs it least, and CORE 07 settles containment by commitment, not by list membership.
- Selection is a fill plus `aria-selected`, and nothing else. Never a border, never a left bar, never a shadow: the row stays the same height in the same place, so the queue does not reflow and there is no second indicator to keep in sync with the first.
- Unread is weight plus the dot, never colour alone. The row already spends colour on the state badge, so a colour-only unread cue would compete with the one signal that is supposed to mean something, and it would vanish for anyone who cannot separate the two hues.
- Two text rows maximum, and density picks the shape: the data rung is one line, the record rung stacks the identity over its qualifier. A third line is the request for a detail pane, not for a taller row.
- One badge lane and one meta lane. A second value column means the surface wanted a table, and a table is `FilterableTable` with the data grid under it, never a queue with extra lanes bolted on.
- Density is placement, not preference. `data` is the 40px rung for scanning volume, `record` is the 44px rung when each row carries an identity and a reason. There is no third rung and no user-facing density switch.
- Nothing in a queue row animates. The fill flips instantly on hover and the action appears instantly, matching `ui/dropdown-menu.tsx`, where the popup transitions and the item under the pointer does not. A 160ms tint smears when a pointer sweeps a two-hundred-row queue.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
