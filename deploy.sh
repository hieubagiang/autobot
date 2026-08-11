#!/usr/bin/env bash
# Push local changes to GitHub (autobot) and pull + restart on the server.
# Usage: ./deploy.sh "optional commit message"
set -euo pipefail
cd "$(dirname "$0")"

SERVER="root@hieuit.top"
REMOTE_DIR="/opt/autobot"
MSG="${1:-chore: update xeca automation}"

echo "==> git add/commit/push (remote: autobot)"
git add -A
git commit -m "$MSG" || echo "(không có gì để commit)"
git push autobot main:main

echo "==> Pulling + restarting services on $SERVER"
ssh "$SERVER" "
  set -e
  cd $REMOTE_DIR
  git pull
  ./venv/bin/pip install --quiet requests beautifulsoup4 playwright
  systemctl restart xeca-watch.service xeca-bot.service
  # cinema-booking-bot.service is enabled but only actually restarted here once
  # CINEMA_TELEGRAM_BOT_TOKEN/CINEMA_TELEGRAM_CHAT_ID exist in .env -- restarting it
  # before that just churns a harmless crash-loop, so this checks first.
  if grep -q '^CINEMA_TELEGRAM_BOT_TOKEN=' .env 2>/dev/null; then
    systemctl restart cinema-booking-bot.service
  fi
  sleep 1
  systemctl --no-pager --lines=0 status xeca-watch.service xeca-bot.service cinema-booking-xvfb.service
"

echo "==> Xong."
