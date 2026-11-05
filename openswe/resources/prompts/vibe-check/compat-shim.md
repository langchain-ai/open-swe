Does `diff` keep an old name or code path alive next to its replacement, such as a deprecated alias, a re-export, or a legacy wrapper kept for backwards compatibility?

Old and new versions of the same thing are both kept.

Code is changed in place, with no aliases or wrappers for the old version.
