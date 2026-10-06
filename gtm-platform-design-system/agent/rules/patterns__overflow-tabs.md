# OverflowTabs

Fit tabs that fit; park the rest under Other + Popover. Never a horizontal ScrollArea on a tab strip — especially not inside a narrow list sidebar.

Import: `@langchain/gtm-platform-design-system/patterns/overflow-tabs`
Source: `src/patterns/overflow-tabs.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### OverflowTabs

| Prop | Type | Required |
|---|---|---|
| `items` | `readonly OverflowTabItem[]` | yes |
| `value` | `string` | yes |
| `onValueChange` | `(next: string) => void` | yes |
| `label` | `string` | yes |
| `overflowLabel` | `string` | no |
| `variant` | `OverflowTabsVariant` | no |
| `icons` | `boolean` | no |
| `maxVisible` | `number` | no |
| `className` | `string` | no |

## The decisions this carries

- Fit tabs that fit; park the rest under Other + Popover. Never a horizontal ScrollArea on a tab strip — especially not inside a narrow list sidebar.
- The selected tab always stays visible. If it would overflow, it swaps into the visible set and something else goes under Other.
- Other is a Popover of the hidden tabs, not a second TabsList and not a route. Choosing an item selects that tab and closes the popover.
- Callers may name the overflow menu and mark secondary views overflowOnly. A selected secondary view is promoted to a visible tab. One description supplies both the tab tooltip and menu explanation.
- An item's icon identifies it in the menu. `icons` also draws it on the visible triggers, for a strip whose options are a vocabulary the reader already meets elsewhere in the same view: the glyph on the tab is then the glyph on the row it filters or groups. It is per strip, never per item, because a set where only some tabs carry a glyph reads as tabs with something missing. The measure row draws the same glyph, so a tab is never parked for width the live trigger does not spend.
- Compose real `Tabs` / `TabsList` / `TabsTrigger` for the visible set so keyboard and active treatment stay on the primitive. `variant` selects the TabsList family: `default` is the rounded segmented track; `ghost` is floating fully-rounded pills (active capsule, no track); `line` is the underline rail.
- TabsList is `w-fit`, triggers are `flex-none`, Other is `shrink-0`. Never `w-full` on the list beside Other — that steals the Other lane and paints the last badge over the word Other (the '0 ther' tell).
- Measure with the live trigger geometry (`h-control-sm` + `px-2.5` + badge). A thinner measure row under-counts width and parks tabs that would have fit.
- Tabs stay mounted while parking changes grid tracks immediately. Resizing and keyboard selection never wait for layout animation.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
