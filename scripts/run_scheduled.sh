#!/bin/zsh
# ==========================================
# Version: 1.0.0
# Date: 2026-09-24
# Summary: Trend Pick ローカル定期実行（ブラウザでX投稿）
# ==========================================
set -euo pipefail

ROOT="/Users/shin/Library/Application Support/trend-pick/app"
cd "$ROOT"

export HOME="/Users/shin"
export USER="shin"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Users/shin/anaconda3/bin:/usr/bin:/bin:$PATH"
export PYTHONPATH="$ROOT"
export LANG="ja_JP.UTF-8"
export LC_ALL="ja_JP.UTF-8"
export X_POST_METHOD="browser"
export X_BROWSER_HEADLESS="1"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright"

LOG_DIR="/Users/shin/Library/Application Support/trend-pick/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/scheduled.log"

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') ===="
  /Users/shin/anaconda3/bin/python main.py --per-source 1 --pool 10 --limit 1
  # 記事を GitHub Pages に反映
  if git status --porcelain docs/ posted.json | grep -q .; then
    git add docs/ posted.json
    git commit -m "chore: update affiliate product articles (local)" || true
    git push || true
  fi
} >>"$LOG" 2>&1
