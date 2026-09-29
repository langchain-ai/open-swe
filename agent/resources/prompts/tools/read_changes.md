List the pull request's changed lines that are still left: not approved, on screen, in Other, or skipped. They come file by file, with a count of what is left overall.

Each line prints as its sign, its line number and its text: `+12` is line 12 of the file at the pull request head, `-7` is line 7 at the merge base. These are the numbers `show_chunk`, `move_to_other` and `skip_changes` take. Files with no text to show, such as binary files, are always in Other.

- `paths`: read only these files. A large pull request is truncated; read the rest a few files at a time.
