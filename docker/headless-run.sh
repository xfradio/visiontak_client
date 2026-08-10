#!/bin/sh
# Start a headless Weston, run the command under it, and leave a screenshot behind.
#
#   headless-run python3 -m visiontak_client --verbose
#
# Weston stands in for Ubuntu Frame: both are Wayland compositors that hand the client
# a single full-screen surface, so this exercises the real UI path without a Pi.
set -eu

WIDTH="${SCREEN_WIDTH:-1920}"
HEIGHT="${SCREEN_HEIGHT:-1080}"
SOCKET="${WAYLAND_DISPLAY:-wayland-1}"
SETTLE="${SETTLE_SECONDS:-12}"

mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"

weston --backend=headless --width="$WIDTH" --height="$HEIGHT" \
       --socket="$SOCKET" --idle-time=0 >/tmp/weston.log 2>&1 &
WESTON_PID=$!

# Wait for the compositor socket rather than sleeping a fixed amount.
for _ in $(seq 1 50); do
  [ -S "$XDG_RUNTIME_DIR/$SOCKET" ] && break
  sleep 0.2
done
if [ ! -S "$XDG_RUNTIME_DIR/$SOCKET" ]; then
  echo "weston failed to start:" >&2
  cat /tmp/weston.log >&2
  exit 1
fi
echo "compositor up on $SOCKET (${WIDTH}x${HEIGHT})"

export WAYLAND_DISPLAY="$SOCKET"
"$@" >/tmp/app.log 2>&1 &
APP_PID=$!

sleep "$SETTLE"

if ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "--- app exited early ---" >&2
  cat /tmp/app.log >&2
  kill "$WESTON_PID" 2>/dev/null || true
  exit 1
fi

cd /out 2>/dev/null || cd /tmp
weston-screenshooter 2>/dev/null || echo "(screenshooter unavailable)" >&2

echo "--- app log ---"
cat /tmp/app.log

kill "$APP_PID" 2>/dev/null || true
kill "$WESTON_PID" 2>/dev/null || true
wait 2>/dev/null || true
