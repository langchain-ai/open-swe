Does `diff` add a retry loop around an operation that will fail the same way every time, or that is not safe to repeat?

Retries hide a persistent error or repeat a side effect.

Retries are limited to transient failures, such as timeouts or rate limits, of operations safe to repeat.
