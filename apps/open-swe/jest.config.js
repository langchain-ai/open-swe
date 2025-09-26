export default {
  preset: "ts-jest/presets/default-esm",
  moduleNameMapper: {
    "^(\\.{1,2}/.*)\\.js$": "$1",
    "^@open-swe/shared$": "<rootDir>/../../packages/shared/src/index.ts",
    "^@open-swe/shared/(.*)$": "<rootDir>/../../packages/shared/src/$1",
    "^@openswe/sandbox-core$": "<rootDir>/../../packages/sandbox-core/src/index.ts",
    "^@openswe/sandbox-core/(.*)$": "<rootDir>/../../packages/sandbox-core/src/$1",
    "^@openswe/sandbox-docker$": "<rootDir>/../../packages/sandbox-docker/src/index.ts",
    "^@openswe/sandbox-docker/(.*)$": "<rootDir>/../../packages/sandbox-docker/src/$1",
  },
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        useESM: true,
      },
    ],
  },
  extensionsToTreatAsEsm: [".ts"],
  setupFiles: ["dotenv/config"],
  passWithNoTests: true,
  testTimeout: 20_000,
  testMatch: ["<rootDir>/src/**/*.test.ts"],
};
