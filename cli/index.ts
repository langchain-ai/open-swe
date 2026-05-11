#!/usr/bin/env node
// open-swe-cli entrypoint. Forwards to src/openswe.tsx (the Ink renderer).
// Top-level errors are routed through the logger; set OPENSWE_DEBUG=1 to
// also print the stack to stderr.

import { config } from 'dotenv';
config();

import { main } from './src/openswe.js';
import { logError } from './src/lib/logger.js';

main().catch(async (error: unknown) => {
  await logError('An unexpected critical error occurred:');
  const detail = error instanceof Error ? (error.stack ?? error.message) : String(error);
  await logError(detail);
  if (process.env.OPENSWE_DEBUG === '1') {
    // eslint-disable-next-line no-console
    console.error(detail);
  }
  process.exit(1);
});
