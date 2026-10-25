# Describe the Stagehand page exposed by the browser run tool

**Verdict: upstream as-is.**

**What:** `managed_deepagents/browser.py` appends `STAGEHAND_PAGE_NOTE` to the run tool's description for both browser scopes, preserving the upstream facade contract verbatim and the conditional thread-local localhost guidance.

**Why:** The callback receives a Stagehand page, whose viewport setter takes two numbers and whose page and locator APIs cover less than Playwright. The note identifies unavailable methods and directs agents to supported locators, DOM evaluation, or snapshot action IDs.

**Upstream:** Describe the actual Stagehand page API in the facade contract. Once that contract includes these limitations and alternatives, remove the appended note.
