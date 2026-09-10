Choose one fixed model profile for this Open SWE turn. Use the least expensive profile likely to complete the whole turn safely.

Profiles, from least to most capable and expensive:

1. fast
- Use for direct lookup, extraction, status checks, test or log collection, mechanical PR or release operations, and localized changes with explicit targets and strong verification.

2. balanced
- Use for ordinary bug fixes, bounded investigations, multi-file implementation, research synthesis, semantic PR maintenance, and partially specified localized work.

3. performance
- Use for architecture or design, requirements disambiguation, subtle semantic review, novel root-cause reasoning, conflicting evidence, cross-component or multi-repository judgment, and high-stakes decisions.

Explicit targets, clear acceptance criteria, reversibility, and strong tests lower the required capability. Ambiguous requirements, weak verification, architectural tradeoffs, broad scope, consequential security or data work, and conflicting assumptions raise it. Prompt length and eventual runtime are not difficulty signals.

Return one model_route for the whole turn.

Current turn:
$task
