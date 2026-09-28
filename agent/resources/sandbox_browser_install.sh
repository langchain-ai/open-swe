# Installs Chromium into the sandbox for the in-app browser. Output streams
# back to the dashboard line by line, so keep it readable.
set -u

if command -v npx >/dev/null 2>&1; then
  if [ "$(id -u)" = "0" ] || sudo -n true 2>/dev/null; then
    echo "Installing Chromium and its system libraries with Playwright..."
    npx --yes playwright@__PLAYWRIGHT_VERSION__ install --with-deps chromium 2>&1
  else
    echo "Installing Chromium with Playwright (no root for system libraries)..."
    npx --yes playwright@__PLAYWRIGHT_VERSION__ install chromium 2>&1
  fi
  exit $?
fi

if command -v apt-get >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
  echo "Installing Chromium with apt-get..."
  apt-get update 2>&1 && apt-get install -y chromium 2>&1
  exit $?
fi

echo "Neither npx nor apt-get is available in this sandbox, so Chromium cannot be installed automatically." >&2
exit 1
