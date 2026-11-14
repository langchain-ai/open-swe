# Document browser facade limits

**Verdict: upstream as-is.**
Browser guidance should describe the supported runtime API.

**What:** `managed_deepagents/browser.py` names unsupported routing, initialization, event and media APIs and gives `page.evaluate` alternatives for fetch mocks, error collection and localStorage setup.
Document-scoped hooks must be installed again after navigation and cannot capture earlier activity.

**Why:** The facade in `stagehand-middleware==0.1.0` invokes `sendCDP` and `onCDP`, which the Stagehand 4.3.0 runtime does not expose; `page.emulateMedia` is also unsupported.
Published middleware releases 0.1.1 and 0.1.2 retain these calls and the `stagehand>=4.3.0,<5` requirement, so their bundle's `packageVersion` of 4.0.6 does not establish a compatible Python dependency pair.
Keep the current pins until compatibility is established.

**Upstream:** Keep accurate capability guidance until the facade and runtime support these APIs together, verified through browser runs; then remove the corresponding exclusions.
