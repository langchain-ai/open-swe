Set which chunks this reader sees next, in order, when they ask for a different order, such as "show me the auth code first".

- `chunks`: chunk numbers from the walkthrough status, in the order to show them. The rest follow in plan order. `[]` goes back to plan order.

It changes only this reader's order; the shared plan stays as it is. Every "Next" from now on follows it.
