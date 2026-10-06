# TableFilters

This popover writes request state. It never filters mounted rows. The table renders the page the server returned for the current values.

Import: `@langchain/gtm-platform-design-system/patterns/table-filters`
Source: `src/patterns/table-filters.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### TableFilters

| Prop | Type | Required |
|---|---|---|
| `fields` | `readonly TableFilterField[]` | yes |
| `values` | `TableFilterValues` | yes |
| `onChange` | `(id: string, value: string \| undefined) => void` | yes |
| `onClear` | `() => void` | no |
| `trigger` | `"button" \| "icon"` | no |
| `tabs` | `TableFilterTabs` | no |
| `catalog` | `TableFilterCatalog` | no |
| `catalogs` | `Readonly<Record<string, TableFilterCatalog>>` | no |

## The decisions this carries

- This popover writes request state. It never filters mounted rows. The table renders the page the server returned for the current values.
- The trigger is an outline Filters button on the toolbar, with a chip count while any value is set. Applied values also render as Quiet badges under the toolbar: option tone when the cell has one, otherwise info; value provider mark or icon when the option has one, otherwise the column icon, then label, display, remove. Leave a `md` gap above that row so it is not flush with the 32px controls.
- The body is Command: first the columns, each with the same icon the grid header uses, then that column's control. Closed enums are static option lists, and each option carries the same glyph or compact ProviderLogo and Quiet tag the cell or row uses. Never derive options from the rows on the page. A 24px ProviderMark well does not belong on this control rung.
- Select is one value; choosing it again clears it. Multi-select is a set: the list stays open, every value toggles, and the field holds them joined so the surface can split them back apart. Text, range and dates commit as they are edited. Range and date values encode as `min..max` / `from..to`. A date filter's calendar fills the popover width.
- A set is as many pills as it has values, never one pill listing them. Each removes only its own value, the trigger counts values rather than fields, and Reset clears the lot. A single pill reading "Rep: three names" makes the reader open the popover to undo one of them, which is the work the pill row exists to save. An option may carry the count of rows behind it, which is a fact about the data and sits quietly after the label; it never replaces the label.
- A standing two-position scope (My / All) is tabs on the Account field (or at the top of this popover when the surface has no Account picker). MY is the default applied filter: it counts on the trigger chip and renders as a Quiet pill. ALL is the explicit unscoped catalog, so it does not. Clearing that pill, or Clear filters, writes ALL.
- Account is the same kind of field as Owner or Region: one row in the column list. A closed field performs no read. Opening it shows My / All as tabs on that field, then one bounded roster page with the same option glyphs the other selects use. The list reuses the accounts cache when the exact scope and filters match, loads the next cursor as it scrolls, and gives server search its own complete query key. Choosing a row writes the filter and a Quiet pill, same as every other field.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
