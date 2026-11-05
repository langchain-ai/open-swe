#!/usr/bin/env bash
set -euo pipefail

playwright test "$@"
playwright test --config playwright.auth.config.ts --pass-with-no-tests "$@"
