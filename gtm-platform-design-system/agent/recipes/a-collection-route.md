# A collection route

A list of things, filtered, that people act on. Runs, tasks, repositories,
accounts. The shape is the same.

## Order of decisions

**1. The filtering is the server's, not the client's.** Before choosing a
component, settle where the work happens. Search, filters, grouping, counts and
cursor paging are request state. A client-side `.filter()` over a fetched array
is a correctness bug waiting for the collection to grow, and `LIMIT` alone is
not a performance proof.

**2. The table surface is `FilterableTable`, never a bespoke composition.**

```tsx
import { FilterableTable } from "@langchain/gtm-platform-design-system/patterns/filterable-table";
```

Read its rules first (`npx design rules filterable-table`): it fixes the
toolbar, how applied filters render, what a group row looks like, and the fact
that a server-filtered table keeps its toolbar mounted and skeletons only the
body. That last one is a feedback regression, not a preference — a toolbar that
unmounts on every keystroke loses focus mid-type.

**3. Cells are typed, not formatted ad hoc.** `SchemaCell` decides what an enum,
a set, money, a date and an absent value look like. Map your field onto a cell
rather than writing a formatter in the column def. Two surfaces formatting the
same field differently is the thing this prevents.

**4. The empty state is a composition, not a centred paragraph.** `EmptyState`.
Distinguish three cases and never collapse them: nothing exists yet, nothing
matches the filters, and the read failed. The third is `StateNotice`.

**5. Row actions belong to the row; collection actions belong to the toolbar.**
A control sits beside the smallest stable region it changes. A bulk action that
appears in a row menu is the most common version of getting this wrong.

**6. Destructive actions are `ConfirmableAction`.** Not a red button, not a local
`confirming` boolean. The button is only the trigger; the decision lives in the
dialog, which states the consequence and never asks anyone to type a name.

## Where this stops

If your collection needs a shape no pattern here answers — a kanban, a timeline,
two lists that must stay in sync — that is a design decision. Say so, point at
the nearest shipped precedent, and do not invent a second table surface.
