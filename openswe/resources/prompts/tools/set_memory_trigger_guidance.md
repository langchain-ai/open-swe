Write the guidance a memory trigger holds for every thread of the workspace, or clear it.

Every thread of the workspace lists the trigger's path and summary, and `recall` of its path, or any path beneath it, returns this guidance as a `workspace` entry.
Use it in place of a workspace prompt line that is true only at the trigger's path, under the same rules: write it fresh, verify it, and supersede the memories it fully covers.
Name the trigger first with `set_memory_trigger`.

Args:
- path: The trigger's path, such as `repo/org/mono/api`.
- guidance: The whole guidance, up to 4,000 characters, replacing what was there; empty to clear it.
