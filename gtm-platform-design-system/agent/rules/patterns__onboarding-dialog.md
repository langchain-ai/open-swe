# OnboardingDialog

OnboardingDialog teaches a surface. WizardDialog remains the create flow. Do not put fields in this dialog.

Import: `@langchain/gtm-platform-design-system/patterns/onboarding-dialog`
Source: `src/patterns/onboarding-dialog.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### OnboardingDialog

| Prop | Type | Required |
|---|---|---|
| `open` | `boolean` | yes |
| `onOpenChange` | `(open: boolean) => void` | yes |
| `eyebrow` | `string` | yes |
| `steps` | `readonly OnboardingStep[]` | yes |
| `step` | `number` | yes |
| `onStepChange` | `(step: number) => void` | yes |
| `onComplete` | `() => void` | yes |
| `completeLabel` | `string` | yes |
| `skipLabel` | `string` | no |

## The decisions this carries

- OnboardingDialog teaches a surface. WizardDialog remains the create flow. Do not put fields in this dialog.
- One Dialog. A step is a visual, a title, and one or two sentences. Never a form and never a third pane.
- The visual is a product miniature or an image the caller owns. It is not a stock illustration and not Orb.
- The title uses text-title. The explanation uses text-meta. Do not borrow text-page.
- Skip, Escape, and finishing the last step have the same outcome: the tour is done. Do not ask again on the next reload. There is no extra close control on the stage.
- The primary names the next commitment: Continue, then the completeLabel the caller passed.
- A step change uses the same 160ms pane enter as the wizard. Forward arrives from the right, back from the left. Reduced motion is a cut.
- A new feature ships a new tour id and its own steps. It never appends to Welcome.
- A step title may link to the surface it names. The href is a product route, never an invented name.
- This pattern does not write storage. The host marks the tour seen.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
