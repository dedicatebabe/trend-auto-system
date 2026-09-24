#!/usr/bin/env python3
# ==========================================
# Version: 1.0.0
# Date: 2026-09-24
# Summary: Trend Pick 用 X ブラウザ初回ログイン
# ==========================================
"""ブラウザを開いて X にログインし、プロファイルを保存する。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.x_browser_poster import (  # noqa: E402
    HOME_URL,
    browser_profile_dir,
    launch_x_context,
)


def main() -> int:
    """ログイン用の可視ブラウザを起動する。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright がありません。次を実行してください:")
        print("  /Users/shin/anaconda3/bin/pip install playwright")
        print(
            '  PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright" '
            "/Users/shin/anaconda3/bin/playwright install chromium"
        )
        return 1

    profile = browser_profile_dir()
    print(f"プロファイル: {profile}")
    print("※ FANZA用とは別プロファイルです（たいち＠old_digital 用）。")
    print("※ 自動投稿と同じ Chromium でログインします（このときだけ画面に出ます）。")
    print("ブラウザが開きます。X にログインしてください。")
    print("ホームが表示されたら、このターミナルで Enter を押すと保存して終了します。")

    with sync_playwright() as p:
        context = launch_x_context(p, headless=False, profile_dir=profile)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded")
        try:
            input()
        except EOFError:
            pass
        print("ログイン状態を保存して閉じます。")
        context.close()
    print("完了。以降は自動投稿がこのログインを使います。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
