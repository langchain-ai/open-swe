Do the lines added in `diff` silence the type checker with casts like `as any` or `as unknown as`, non-null assertions, `cast(Any, ...)`, or by widening a type to any?

A type error is hidden instead of fixed.

Types are narrowed with real checks, or no casts are added.
