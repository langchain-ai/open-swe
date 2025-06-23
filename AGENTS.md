<general_rules>
- Always use Yarn 3.5.1 as the package manager - never use npm or other package managers
- Run all commands from the repository root using Turbo orchestration (yarn dev, yarn build, yarn lint, yarn format)
- Before creating new utilities or shared functions, search in packages/shared/src to see if one already exists
- When importing from the shared package, use the @open-swe/shared namespace with specific module paths
- Always copy .env.example files to .env and fill in required values before running applications
- Follow strict TypeScript practices - the codebase uses strict mode across all packages
- Use ESLint and Prettier for code quality - run yarn lint:fix and yarn format before committing
- Console logging is prohibited in the open-swe app (ESLint error) - use proper logging mechanisms instead
- Build the shared package first before other packages can consume it (yarn build handles this automatically)
- Use proper Git practices - all changes are auto-committed, work only within the existing repository
- Follow existing code patterns and maintain consistency with the established architecture
</general_rules>

<repository_structure>
This is a Yarn workspace monorepo with Turbo build orchestration containing three main packages:

**apps/open-swe**: LangGraph agent application
- Core LangChain/LangGraph agent implementation with TypeScript
- Contains three graphs: programmer, planner, and manager (configured in langgraph.json)
- Uses strict ESLint rules including no-console errors
- Requires environment variables for GitHub tokens and API keys

**apps/web**: Next.js 15 web interface
- React 19 frontend with Radix UI components and Tailwind CSS
- Modern web stack with TypeScript, ESLint, and Prettier with Tailwind plugin
- Serves as the user interface for the LangGraph agent

**packages/shared**: Common utilities package
- Central workspace dependency providing shared types, constants, and utilities
- Exports modules via @open-swe/shared namespace (e.g., @open-swe/shared/open-swe/types)
- Must be built before other packages can import from it
- Contains crypto utilities, GraphState types, and open-swe specific modules

**Root Configuration**:
- turbo.json: Build orchestration with task dependencies and parallel execution
- .yarnrc.yml: Yarn 3.5.1 configuration with node-modules linker
- tsconfig.json: Base TypeScript configuration extended by all packages
</repository_structure>

<dependencies_and_installation>
**Package Manager**: Use Yarn 3.5.1 exclusively (configured in .yarnrc.yml)

**Installation Process**:
1. Run `yarn install` from the repository root - this handles all workspace dependencies automatically
2. Copy .env.example files to .env in both apps/open-swe and apps/web directories
3. Fill in required environment variables (GITHUB_TOKEN_ENCRYPTION_KEY must match between apps)
4. Run `yarn build` to build all packages in correct dependency order

**Key Dependencies**:
- LangChain ecosystem: @langchain/langgraph, @langchain/anthropic for agent functionality
- Next.js 15 with React 19 for web interface
- Radix UI and Tailwind CSS for component library and styling
- TypeScript with strict mode across all packages
- Jest with ts-jest for testing framework

**Workspace Structure**: Dependencies are managed at the root level with version resolutions enforced globally. Individual packages reference the shared package via @open-swe/shared workspace dependency.
</dependencies_and_installation>

<testing_instructions>
**Testing Framework**: Jest with TypeScript support via ts-jest preset and ESM module handling

**Test Types**:
- Unit tests: *.test.ts files (e.g., take-action.test.ts in __tests__ directories)
- Integration tests: *.int.test.ts files (e.g., sandbox.int.test.ts)

**Running Tests**:
- `yarn test` - Run unit tests across all packages
- `yarn test:int` - Run integration tests (apps/open-swe only)
- `yarn test:single <file>` - Run a specific test file

**Test Configuration**:
- 20-second timeout for longer-running tests
- Environment variables loaded via dotenv integration
- ESM module support with .js extension mapping
- Pass-with-no-tests setting for CI/CD compatibility

**Writing Tests**: Focus on testing core business logic, utilities, and agent functionality. Integration tests should verify end-to-end workflows. Use the existing test patterns and maintain consistency with the established testing structure.
</testing_instructions>

