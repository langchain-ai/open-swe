# ProviderAction

A provider hop is a compact outline control with the shipped ProviderLogo and a verb. Do not draw a second local Salesforce or LinkedIn button.

Import: `@langchain/gtm-platform-design-system/patterns/provider-action`
Source: `src/patterns/provider-action.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### ProviderAction

| Prop | Type | Required |
|---|---|---|
| `href` | `string` | yes |
| `label` | `string` | yes |
| `provider` | `ProviderLogoId` | yes |

## The decisions this carries

- A provider hop is a compact outline control with the shipped ProviderLogo and a verb. Do not draw a second local Salesforce or LinkedIn button.
- The href is a server https URL. Never assemble one from a Salesforce id or a LinkedIn public id in the browser.
- Open in a new tab with rel=noopener noreferrer. Stop row clicks so a hop inside a list or peek does not also select the row.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
