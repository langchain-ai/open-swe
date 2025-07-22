# Open SWE Shared

Shared utilities and types package for Open SWE, providing common functionality used across the monorepo applications.

## Documentation

For information about the monorepo structure and shared packages, see the [monorepo documentation](https://docs.langchain.com/labs/swe/setup/monorepo).

## Development

1. Install dependencies: `yarn install`
2. Build the shared package: `yarn build`

The shared package must be built before other packages can import from it.
