#!/bin/sh
# Two-device HTTPS preview of the local app through a tunnel. Prepared for review: run it only when a preview is authorized.
#   CALLIOPE_BASIC_AUTH=tester:<password> scripts/preview.sh [ngrok|cloudflared]
# Testers sign in with the basic-auth password (the app); a friend's /s/<id> link opens without it. Spending
# comes from the standing authorization (state/authorization.json) unless CALLIOPE_LIVE_UNITS / CALLIOPE_NOVA_USD are set.
# The app still binds to 127.0.0.1; only the tunnel reaches it. Use photos of people who agreed to it. Stop with Ctrl-C.
set -e
: "${CALLIOPE_BASIC_AUTH:?Set CALLIOPE_BASIC_AUTH=user:password so only testers can use the app}"
cd "$(dirname "$0")/.."
npm run build >/dev/null
PORT=${PORT:-8765}
CALLIOPE_SECURE_COOKIE=1 PORT=$PORT .venv/bin/python app.py &
APP=$!
trap 'kill $APP 2>/dev/null' EXIT INT TERM
sleep 2
case "${1:-ngrok}" in
  cloudflared) cloudflared tunnel --url "http://127.0.0.1:$PORT" ;;      # no account; brew install cloudflared
  *) ngrok http "$PORT" ;;                                               # free plans show a one-time warning page to each browser
esac
