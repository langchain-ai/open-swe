# EmptyState

Compose `ui/empty` (Header → Media → Title → Description, optional Content). Never a bare centred `<p>` pretending to be an empty surface. Reference: ReUI `c-empty-*` under `web/reference/reui/empty/`.

Import: `@langchain/gtm-platform-design-system/patterns/empty-state`
Source: `src/patterns/empty-state.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### EmptyState

| Prop | Type | Required |
|---|---|---|
| `title` | `string` | yes |
| `description` | `string` | no |
| `icon` | `Glyph` | no |
| `media` | `ReactNode` | no |
| `action` | `ReactNode` | no |
| `className` | `string` | no |

## The decisions this carries

- Compose `ui/empty` (Header → Media → Title → Description, optional Content). Never a bare centred `<p>` pretending to be an empty surface. Reference: ReUI `c-empty-*` under `web/reference/reui/empty/`.
- Media is still: a muted glyph well (`icon`) or a sanctioned brand mark tile (`media`). Never mount `ui/orb` / thinking-orbs, Spinner, or continuous orbit/breathe decoration: an empty surface is settled, and filter empties are seen too often for ambient motion.
- Title is `text-title` semibold; description is `text-body` ink-subtle. One primary action in Content when recovery is possible; omit Content when the surface already owns the next step (e.g. Chat composer below).
- Copy stays short and plain: one short title, one short sentence max. No jargon (ledger, upstream, status-filtered, bridge), no over-explaining how the system works. Prefer silence over a second sentence.
- Example prompts and recovery cards are controls, not decoration. Activating one must start the exact flow its copy promises, preserve any composed text, and provide the same keyboard and touch path as a primary action. If the route cannot perform the action, omit the card and say where the action lives.
- No enter/exit choreography on the empty itself. If the surface swaps list ↔ empty, the swap is instant — empty is a state, not a celebration.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
