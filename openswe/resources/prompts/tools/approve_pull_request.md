Approve the pull request on GitHub as the person whose message started this turn. Call it only when their latest message asks for an approval; it works at any point in the walkthrough, whether or not every chunk was shown.

- `body`: optional review text. Leave it empty for the default note.

It fails on a turn nobody in the channel started, such as a pull request update, and when that person has no GitHub account linked to Open SWE.
