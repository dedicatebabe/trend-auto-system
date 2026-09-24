#!/bin/zsh
# ==========================================
# Version: 1.0.0
# Date: 2026-09-24
# Summary: Trend Pick のローカル自動投稿を有効化する
# ==========================================
set -euo pipefail

REPO="/Users/shin/Desktop/myproject/アフィリエイト/trend-auto-system"
SUPPORT="$HOME/Library/Application Support/trend-pick"
PLIST_SRC="$REPO/launchd/com.trendpick.bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.trendpick.bot.plist"

mkdir -p "$SUPPORT/logs" "$HOME/Library/LaunchAgents"

# FANZA と同様、ASCII パス経由で実行できるようにする
ln -sfn "$REPO" "$SUPPORT/app"
cp "$REPO/scripts/run_scheduled.sh" "$SUPPORT/run_scheduled.sh"
chmod +x "$SUPPORT/run_scheduled.sh" "$REPO/scripts/x_browser_login.py" "$REPO/scripts/run_scheduled.sh"

echo "依存関係を確認します..."
/Users/shin/anaconda3/bin/pip install -q -r "$REPO/requirements.txt"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright"
/Users/shin/anaconda3/bin/playwright install chromium

# .env にブラウザ既定を追記（なければ）
ENV_FILE="$REPO/.env"
if [[ -f "$ENV_FILE" ]]; then
  if ! grep -q '^X_POST_METHOD=' "$ENV_FILE"; then
    printf '\nX_POST_METHOD=browser\nX_BROWSER_HEADLESS=1\n' >> "$ENV_FILE"
    echo ".env に X_POST_METHOD=browser を追加しました"
  fi
fi

cp "$PLIST_SRC" "$PLIST_DST"
launchctl bootout "gui/$(id -u)/com.trendpick.bot" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable "gui/$(id -u)/com.trendpick.bot"

echo ""
echo "セットアップ完了。"
echo "次に一度だけログインしてください:"
echo "  cd \"$REPO\""
echo "  /Users/shin/anaconda3/bin/python scripts/x_browser_login.py"
echo ""
echo "動作確認（1件・画面あり）:"
echo "  X_BROWSER_HEADLESS=0 /Users/shin/anaconda3/bin/python main.py --limit 1"
echo ""
echo "スケジュール: 8/12/16/20/0時（各1件）"
echo "ログ: $SUPPORT/logs/scheduled.log"
