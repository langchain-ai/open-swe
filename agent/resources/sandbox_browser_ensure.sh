# Starts (or finds) the headless Chromium the in-app browser attaches to.
# Runs inside the thread's sandbox over the sandbox exec API and prints one
# JSON status line last: {"status":"ready"|"missing"|"failed",...}.
set -u

PORT=__PORT__
PROFILE_DIR="${OPEN_SWE_BROWSER_PROFILE:-/tmp/open-swe-browser}"
LOG_FILE="$PROFILE_DIR/chromium.log"

alive() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1
    return
  fi
  python3 - "$PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/json/version", timeout=2)
PY
}

find_browser() {
  local candidate
  for candidate in chromium chromium-browser google-chrome google-chrome-stable chrome; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  local cache="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
  for candidate in \
    "$cache"/chromium-*/chrome-linux*/chrome \
    "$cache"/chromium_headless_shell-*/chrome-linux*/headless_shell \
    "$HOME"/.cache/puppeteer/chrome/*/chrome-linux64/chrome; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

json_escape() {
  printf '%s' "$1" | tr '\n' ' ' | sed 's/\\/\\\\/g; s/"/\\"/g'
}

if alive; then
  echo '{"status":"ready"}'
  exit 0
fi

BROWSER="$(find_browser)" || {
  echo '{"status":"missing"}'
  exit 0
}

mkdir -p "$PROFILE_DIR"
ARGS=(
  --headless=new
  "--remote-debugging-port=$PORT"
  --remote-debugging-address=127.0.0.1
  "--remote-allow-origins=*"
  "--user-data-dir=$PROFILE_DIR"
  --no-first-run
  --no-default-browser-check
  --disable-dev-shm-usage
  --disable-gpu
  --disable-background-timer-throttling
  --window-size=1280,800
)
# Chromium refuses to run its own sandbox as root; the process is already
# confined to the LangSmith sandbox, which is the isolation that matters here.
if [ "$(id -u)" = "0" ]; then
  ARGS+=(--no-sandbox)
fi

nohup setsid "$BROWSER" "${ARGS[@]}" about:blank >"$LOG_FILE" 2>&1 </dev/null &

for _ in $(seq 1 75); do
  if alive; then
    echo "{\"status\":\"ready\",\"browser\":\"$(json_escape "$BROWSER")\"}"
    exit 0
  fi
  sleep 0.2
done

echo "{\"status\":\"failed\",\"browser\":\"$(json_escape "$BROWSER")\",\"log\":\"$(json_escape "$(tail -c 800 "$LOG_FILE" 2>/dev/null)")\"}"
