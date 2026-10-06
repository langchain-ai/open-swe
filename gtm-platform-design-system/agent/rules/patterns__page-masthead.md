# PageMasthead

A masthead is content, not chrome. It states what today is and what is in it; PageBand states where you are and what you can do. That is why Home mounts a masthead and no band, and why a surface that already carries a lineage band does n...

Import: `@langchain/gtm-platform-design-system/patterns/page-masthead`
Source: `src/patterns/page-masthead.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### PageMasthead

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `description` | `string` | no |
| `icon` | `Glyph` | no |
| `counts` | `readonly PageMastheadCount[]` | no |

## The decisions this carries

- A masthead is content, not chrome. It states what today is and what is in it; PageBand states where you are and what you can do. That is why Home mounts a masthead and no band, and why a surface that already carries a lineage band does not also get a masthead: the two would say the page's name twice, which is the same failure as a second band.
- Counts ride in the masthead, never in the band. A count is a reading of the page's contents and belongs under the sentence that describes them, on their own line after the title and the description; the band is chrome and chrome may only assert location, depth, and verbs (`PAGE_BAND_RULES`). Counts arrive as data, not as children, so a call site cannot smuggle a control into the lane. They sit under the title rather than trailing it because the title row is the one lane a surface may also give a period or scope control, and a reading and a control competing for the same line is how that row wraps.
- A count with an href is a router, not a verb. It opens the owning surface the same way a Home queue row does (platform 1.2), which is how a number stops being decoration. A glyph or a provider mark may ride in the Quiet badge; both arrive as data on the count. A count without an href stays a reading. Do not invent a destination, and do not promote the lane into KPI tiles: a number is a row or a cell, never a card (`STAT_READOUT_RULES`).
- Counts are Quiet badges and there is no tier prop. One Urgent per region is already the badge law, and a masthead sits above a queue that will legitimately want the region's Urgent for the one thing that is actually urgent. A count is orientation, so it never spends that budget.
- `text-page` is the surface's one borrowed rung. Wiki 02: a surface spends two sizes and borrows a third once. The masthead is where Home spends the borrow, which is what forbids a second `text-page` element anywhere else on the same route.
- The description sits two rungs below the title: `text-page` pairs with `text-body`, exactly as PageFrame's header pairs them. One line. A description that needs a second line wanted to be a section.
- The masthead has no actions slot, and that is the decision, not an omission. Verbs belong to the band on object routes and to the section that owns them everywhere else; a title strip that grows a primary button becomes the action lane Home already deleted (`surface-decisions`, platform 1.2). A count-as-link is not that slot.
- A named Agent feature may pass a leading glyph on the title row (`Icon` `lg`, same construction as `ListSidebarTitle`). It names the feature. It is not a verb and it does not replace the h1.
- The masthead takes no width prop, for the same reason PageFrame does not: measure belongs to AppShell (`APP_SHELL_RULES`). The strip fills whatever the route was given, which is the whole point of extracting it from a frame that is welded to the 768 reading column.
- OPEN, FILED: the boards draw the editorial title at 26px, which is above the five-pair ladder and below nothing that could carry it. It ships on `text-page` (20px) until a designer rules, because adding a sixth rung needs a decision and two consumers in the same PR. Do not set 26px locally — that is the type-pair law's exact failure mode. (annex 06, Home.)

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
