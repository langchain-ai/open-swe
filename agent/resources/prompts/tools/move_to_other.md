Send lines that are still left to Other without showing anything, or take lines back out of Other.

- `files`: the lines, per `path`, with `added` and `deleted` ranges as `read_changes` prints them.
- `back`: `true` to take these lines out of Other, so they are left again and can be shown.

Use it when you notice noise, such as a whole lockfile or every import in a file, or when the reader asks to see something you had put in Other.
