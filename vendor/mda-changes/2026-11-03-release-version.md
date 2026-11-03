# Not upstream as-is.

The vendored Python manifest declares `0.10.1`, matching the published stable release from upstream tag `v0.10.1` (`9ab1d836a401d0d791c9af34bb9a5b35bb9aad6e`). Upstream's release builder stamps `MDA_VERSION` into its manifest while packaging; the tagged source still declares `0.10.0`.

Open SWE installs this source editably, without that packaging step, so it needs the published version in `packages/pypi/pyproject.toml` as well as the refreshed release source. Existing runtime patches and the carried upstream PR remain intact.

Upstream: drop this local stamp when the source manifest itself declares the selected published version, or repeat the upstream packaging stamp when refreshing to another release.
