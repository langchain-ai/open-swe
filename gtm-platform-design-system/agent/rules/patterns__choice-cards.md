# ChoiceCards

A fork is cards, not a radio list and not two forms stacked. Each card names the path and what it opens.

Import: `@langchain/gtm-platform-design-system/patterns/choice-cards`
Source: `src/patterns/choice-cards.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ChoiceCards

| Prop | Type | Required |
|---|---|---|
| `describedBy` | `string` | no |
| `labelledBy` | `string` | yes |
| `onChange` | `(value: Value) => void` | yes |
| `options` | `readonly ChoiceCardOption<Value>[]` | yes |
| `parse` | `(value: string) => value is Value` | yes |
| `value` | `Value \| null` | yes |

## The decisions this carries

- A fork is cards, not a radio list and not two forms stacked. Each card names the path and what it opens.
- The card is the control. A radio mark never appears. The exclusive group stays for the keyboard and the screen reader.
- Selecting a card opens that path alone. Returning to the cards is Back, never a second form appearing underneath.
- A card carries an icon, a title, and a description. It never shows keys, schema names, or source types.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
