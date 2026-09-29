List the pull request's changed lines the reader has not approved yet, file by file.

Each line prints as its sign, its line number and its text: `+12` is line 12 of the file at the pull request head, `-7` is line 7 at the merge base. These are the numbers `plan_walkthrough` takes. Files with no text to show, such as binary files, are listed without lines and always go to Other.

- `paths`: read only these files. A large pull request is truncated; read the rest a few files at a time.
