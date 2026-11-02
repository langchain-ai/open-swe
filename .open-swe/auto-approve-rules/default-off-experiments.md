Open SWE may approve a pull request that has no unresolved findings and only changes behavior behind a feature flag (experiment) that is off by default, with evidence that every changed behavior affects only users who explicitly enable it.
Verify the flag's default and every affected execution path, including shared code, background jobs, and work performed before checking the flag; the flag's presence alone is not proof of isolation.
The change must not enable the flag by default or alter behavior for users who have not opted in, and must not introduce undue production storage, compute, or memory risk, even when enabled.
If isolation, the default, or production resource risk is ambiguous or cannot be established, require human review.
This rule does not waive any human-review requirement in the other rules.
