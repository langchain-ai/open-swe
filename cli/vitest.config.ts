/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'url';
import path from 'path';

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test-setup.ts'],
  },
  resolve: {
    alias: [
      { find: /^@app\//, replacement: r('./src/app/') },
      { find: /^@tui\//, replacement: r('./src/tui/') },
      { find: /^@agent\//, replacement: r('./src/agent/') },
      { find: /^@lib\//, replacement: r('./src/lib/') },
      { find: /^@config\//, replacement: r('./src/config/') },
      { find: /^@types$/, replacement: r('./src/types/index.ts') },
      { find: /^@types\//, replacement: r('./src/types/') },
    ],
  },
});
