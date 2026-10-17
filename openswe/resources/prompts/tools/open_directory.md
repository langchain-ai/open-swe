Open the directory you are working in: the repository checkout, or in a monorepo the package within it.

Call this once a repository is checked out and before reading or changing its files; editing tools refuse until a directory is open. From then on every command runs from this directory and relative file paths resolve from it, so prefer relative paths. Open another directory whenever the work moves to another repository or package.

Returns the directory's absolute `path`, the GitHub `repo` (`owner/name`) whose checkout holds it, and its `subpath` within that checkout (`""` at the root). `repo` is null when the checkout has no GitHub `origin`, and `repo` and `subpath` are both null outside a git checkout.
