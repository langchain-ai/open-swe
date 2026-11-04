Classify a state with TypeSafe's System One model, through the LangSmith LLM Gateway, in one fast request (about 70–500ms).

System One models make structured decisions instead of generating text: given a state and named questions, the model answers every question in one parallel pass with calibrated probabilities.
An answer is always one of the options you defined, never free text, and higher confidence reliably means higher accuracy.
Use it as a smart `if` statement for labeling, routing, triage, scoring, or yes/no checks over text or JSON, where an LLM call would be slow or need parsing.
Gate actions on confidence (for example, act only above 0.8) and give the low-confidence tail a closer look.
Call it from code mode to classify many items in a loop or in parallel.

Args:
- state: What the questions are about: text, a JSON object, or a JSON array.
- questions: Questions by name, each with a `type`, complete `instructions`, and `criteria`:
  - `choice`: pick one label; `criteria` maps each label to when it applies (or null when the label says enough).
    Add an `other` label when the labels may not cover every input.
  - `noul`: yes/no; `criteria` is optional, `{"true": "...", "false": "..."}`.
  - `score`: an ordered rubric; `criteria` is a list of at least two levels, lowest first.

Returns: `success`, `usage`, and `answers` keyed by question name.
A `choice` answer has `choice`, `probabilities` per label, and `confidence`; a `noul` answer has `noul`, the probability of yes; a `score` answer has `score` (an expected level, possibly fractional), `legend`, `probabilities` per level, and `confidence`.
Confidence measures how concentrated the distribution is, not that the answer is correct.
On failure, `success` is false and `error` says why.
