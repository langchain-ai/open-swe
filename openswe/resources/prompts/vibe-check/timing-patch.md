Does `diff` add a fixed sleep, or raise a timeout, so that a test or a startup race stops failing, instead of waiting for the condition it needs?

A timing problem is papered over with a longer wait.

Waits poll for a condition with a deadline.
Backoff delays between retries are a different matter.
