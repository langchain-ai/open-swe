import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Library-mode build: the sandbox this app runs in evaluates a single
// dependency-free CJS file exporting { render(data, root, metadata) } — not a
// self-mounting SPA. See src/entry.tsx and AGENTS.md.
export default defineConfig({
  plugins: [react()],
  // Unlike Vite's normal app-mode build, library mode does NOT replace
  // process.env.NODE_ENV on its own — React/ReactDOM's bundled source still
  // references it internally. There's no `process` global in the sandboxed
  // iframe (or any browser), so without this the bundle throws
  // "ReferenceError: process is not defined" the moment it's required.
  define: {
    'process.env.NODE_ENV': '"production"',
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    lib: {
      entry: 'src/entry.tsx',
      formats: ['cjs'],
      fileName: () => 'bundle.js',
    },
    // The sandbox loads only bundle.js; lazy chunks (CodeLite languages) would 404.
    rolldownOptions: { output: { codeSplitting: false } },
  },
});
