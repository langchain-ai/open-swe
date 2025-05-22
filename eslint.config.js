import eslint from '@eslint/js';
import globals from "globals";
import tseslint from "typescript-eslint";
import eslintImport from "eslint-plugin-import";
import eslintNoInstanceof from "eslint-plugin-no-instanceof";

export default tseslint.config(
  eslint.configs.recommended,
  tseslint.configs.recommendedTypeChecked,
  { ignores: ["dist", "eslint.config.js"] },
  {
    files: ["**/*.ts"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
      globals: {
        ...globals.node,
        ...globals.browser,
      },
    },
    plugins: {
      import: eslintImport,
      "no-instanceof": eslintNoInstanceof,
    },
    rules: {
      "@typescript-eslint/explicit-module-boundary-types": 0,
      "@typescript-eslint/no-empty-function": 0,
      "@typescript-eslint/no-shadow": 0,
      "@typescript-eslint/no-unsafe-assignment": 0,
      "@typescript-eslint/no-unsafe-member-access": 0,
      "@typescript-eslint/no-unsafe-return": 0,
      "@typescript-eslint/no-unsafe-argument": 0,
      "@typescript-eslint/no-empty-interface": 0,
      "@typescript-eslint/no-use-before-define": ["error", "nofunc"],
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_|^UNUSED_",
          caughtErrorsIgnorePattern: "^_",
          destructuredArrayIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/await-thenable": "error",
      "@typescript-eslint/no-explicit-any": 0,
      camelcase: 0,
      "class-methods-use-this": 0,
      "import/extensions": [2, "ignorePackages"],
      "import/no-extraneous-dependencies": [
        "error",
        { devDependencies: ["**/*.test.ts"] },
      ],
      "import/no-unresolved": 0,
      "import/prefer-default-export": 0,
      "keyword-spacing": "error",
      "max-classes-per-file": 0,
      "max-len": 0,
      "no-await-in-loop": 0,
      "no-bitwise": 0,
      "no-console": 0,
      "no-restricted-syntax": 0,
      "no-shadow": 0,
      "no-continue": 0,
      "no-underscore-dangle": 0,
      "no-use-before-define": 0,
      "no-useless-constructor": 0,
      "no-return-await": 0,
      "consistent-return": 0,
      "no-else-return": 0,
      "new-cap": ["error", { properties: false, capIsNew: false }],
    },
  },
);
