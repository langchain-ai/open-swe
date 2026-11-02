Classify a state with Jev, TypeSafe's calibrated classification model, in one fast request (about 70–500ms).

Use it for structured decisions where an LLM call would be slow or need parsing: labeling, routing, triage, scoring, or yes/no checks over text or JSON.
Jev never invents an answer outside the options you define, and its probabilities are calibrated, so gate actions on them (for example, act only when confidence is above 0.8).
Call it from code mode to classify many items in a loop or in parallel.

Args:
- state: What the questions are about: text, or structured JSON.
- questions: Questions by name, each with:
  - type: `choice` (pick one of up to 255 labels), `noul` (yes/no), or `score` (an ordered scale).
  - instructions: What is being decided.
  - criteria: For `choice`, a map from each label to a description of when it applies (or null when the label says enough). For `noul`, optionally `{"true": "...", "false": "..."}`. For `score`, a list of at least two levels, lowest first.
- model: The Jev model (default `jev-1.13.0`; `jev-latest` follows the newest release).

Returns: `success`, `model`, `usage`, and `answers` keyed by question name.
A `choice` answer has `choice`, `probabilities` per label, and `confidence`; a `noul` answer has `noul`, the probability of yes; a `score` answer has `score`, `probabilities` keyed by level position, and `confidence`.
On failure, `success` is false and `error` says why.
