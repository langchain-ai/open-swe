# ChangeFeed

ChangeFeed is the quieter Home band under the DecisionRow queue. It is observational — no Approve CTA, no action lane. Home is a router, so a row that can name a destination opens that surface on the whole row, the same verb Coming up uses.

Import: `@langchain/gtm-platform-design-system/patterns/change-feed`
Source: `src/patterns/change-feed.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ChangeFeed

| Prop | Type | Required |
|---|---|---|
| `items` | `readonly ChangeFeedItem[]` | yes |
| `collapsedCount` | `number` | no |
| `className` | `string` | no |

### ListContinuation

| Prop | Type | Required |
|---|---|---|
| `expanded` | `boolean` | yes |
| `onToggle` | `() => void` | yes |
| `testId` | `string` | yes |

## The decisions this carries

- ChangeFeed is the quieter Home band under the DecisionRow queue. It is observational — no Approve CTA, no action lane. Home is a router, so a row that can name a destination opens that surface on the whole row, the same verb Coming up uses.
- RECEIPT
- WORLD
- A receipt renders in the compact inline Receipt form (`surface-decisions` §6): the same hairline rung, prefixed by the receipt marker so agent-authored facts stay distinguishable from system facts. The Frame-shelled `Receipt` is the inspectable detail one hop away — it never appears in this band.
- Flat, not a Frame. `PAGE_SECTION_RULES` settles it: the decision queue is a panel; 'Changes to know' is a window onto something continuous. A bordered card with matching left/right chrome pad is a containment lie.
- Each item: relative time · marker · object · one-line change on the 44px record rung, hairline between rows, `px-3` once — same inset as DecisionRow. The marker lane is reserved on every row and filled only on receipts, so both halves share one column grid. Compose DiffRow when a field mutation needs structure.
- 'Compressed' is five rows on the page and View more. View more expands the same panel in place, capped, and the list scrolls inside. The trigger stays outside the scroll. It is not a popover, and it is not a pager. The continuation is band chrome on the quieter 40px rung, not the action lane the first rule bans.
- `GET /v1/activity` is the briefing this band renders. `/v1/changes` is the invalidation log, ids only. Never join the queue, upcoming, drafts, threads, or alerts in the page to decorate a row. If the band needs a title or href the briefing does not serve, the endpoint moves. A row opens the owning surface when the briefing names an href.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
