import unicorn from 'eslint-plugin-unicorn';
import typescriptEslint from '@typescript-eslint/eslint-plugin';
import parser from '@typescript-eslint/parser';

export default [
  {
    files: ['**/*.ts', '**/*.tsx'],
    plugins: {
      unicorn,
      '@typescript-eslint': typescriptEslint,
    },
    languageOptions: {
      parser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: {
          jsx: true,
        },
      },
    },
    rules: {
      // enforce kebab-case, except PascalCase for React components
      'unicorn/filename-case': ['error', {
        cases: {
          kebabCase: true,
          pascalCase: true,
        },
        // Allow PascalCase only for .tsx files (components)
        ignore: [/^[A-Z].*\.tsx$/],
      }],
    },
  },
];