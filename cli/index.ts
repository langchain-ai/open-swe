#!/usr/bin/env node

import { config } from 'dotenv';
config();

import { main } from './src/coda.js';
import { logError } from './src/lib/logger.js';

main().catch(async (error) => {
  await logError('An unexpected critical error occurred:');
  if (error instanceof Error) {
    await logError(error.stack ?? String(error));
  } else {
    await logError(String(error));
  }
  process.exit(1);
});
