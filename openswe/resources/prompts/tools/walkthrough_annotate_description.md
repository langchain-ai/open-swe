Annotate the pull request's description: tie each of its sentences that describes the change to the hunks it talks about, so readers of the review page can see the code behind a sentence by hovering it.

- `sentences`: one entry per annotated sentence.
  - `sentence`: one whole sentence copied exactly from the description, markdown included, without a list marker or heading `#` in front.
  - `hunks`: the hunks it refers to, per `path`: each named in `hunks` by its first changed line, such as `+12` or `-40`, as the plan status lists them.
    Any hunk can be named, whatever chunk or Other holds it.

Leave out sentences that point at no code, such as motivation, links and screenshots.
Each call replaces every earlier annotation, so always send the complete set.

Returns how many sentences are annotated and the plan as it now stands.
