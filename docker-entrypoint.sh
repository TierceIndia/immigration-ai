#!/bin/sh
# Starts a real Xvfb X server directly (not via xvfb-run) and execs the real
# command (alembic/seed/uvicorn) with DISPLAY set, so browser_fetcher.py's
# headless=False Chromium has a real display to attach to.
#
# xvfb-run's own wrapper was tried first and hangs under Docker: it waits on a
# SIGUSR1 "Xvfb is ready" signal that never reliably arrives without a proper
# init process forwarding signals to PID 1's subshells. Starting Xvfb directly
# and polling for its socket file avoids that signal-handling dependency
# entirely and is the standard, more robust pattern for Docker specifically.
set -e

DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"

Xvfb "${DISPLAY}" -screen 0 1280x1024x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!

# Poll for the X socket instead of a fixed sleep — fast on a healthy start,
# still bounded so a genuinely broken Xvfb fails fast instead of hanging forever.
tries=50
while [ $tries -gt 0 ]; do
    if [ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
        break
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "docker-entrypoint: Xvfb exited before becoming ready" >&2
        cat /tmp/xvfb.log >&2
        exit 1
    fi
    tries=$((tries - 1))
    sleep 0.1
done

if [ $tries -eq 0 ]; then
    echo "docker-entrypoint: timed out waiting for Xvfb socket" >&2
    cat /tmp/xvfb.log >&2
    exit 1
fi

exec "$@"
