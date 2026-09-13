# ==========================================
# Version: 1.0.1
# Date: 2026-09-13
# Summary: offline 実行と依存の遅延 import に対応
# ==========================================
"""
一般向けトレンドトピックから GitHub Pages 記事を生成し、
任意で X 投稿まで行うメインスクリプト。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

from modules.affiliate_links import attach_affiliate_urls
from modules.ai_generator import extract_card_summary
from modules.page_builder import (
    build_cushion_page_url,
    write_article_and_update_index,
)
from modules.topic_source import pick_topic

POSTED_JSON = "posted.json"
POSTED_RETENTION_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trend-auto-system")


def project_root() -> Path:
    return Path(__file__).resolve().parent


def load_posted_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("posted.json の読み込みに失敗: %s", exc)
        return []
    posts = data.get("posts", [])
    if not isinstance(posts, list):
        return []
    return [p for p in posts if isinstance(p, dict)]


def save_posted_history(path: Path, posts: list[dict]) -> None:
    path.write_text(
        json.dumps({"posts": posts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def topic_ids_posted_within_days(posts: list[dict], days: int) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    blocked: set[str] = set()
    for row in posts:
        tid = str(row.get("topic_id", "")).strip()
        if not tid:
            continue
        raw_time = row.get("posted_at")
        if not raw_time:
            blocked.add(tid)
            continue
        try:
            posted_at = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
        except ValueError:
            blocked.add(tid)
            continue
        if posted_at >= cutoff:
            blocked.add(tid)
    return blocked


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trend Pick 自動アフィリエイト実行")
    parser.add_argument(
        "--category",
        choices=("anime", "gadget", "goods", "game", "any"),
        default="any",
        help="優先カテゴリ（any で順次）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="X 投稿と posted.json 更新をスキップ（ページ生成のみ）",
    )
    parser.add_argument(
        "--skip-x-sleep",
        action="store_true",
        help="X 投稿前のランダム待機をスキップ",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Gemini を使わずフォールバック本文で生成",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    load_dotenv(root / ".env")

    try:
        pages_base = require_env("BASE_URL")
        preferred = None if args.category == "any" else args.category

        posted_path = root / POSTED_JSON
        history = load_posted_history(posted_path)
        skip_ids = topic_ids_posted_within_days(history, POSTED_RETENTION_DAYS)
        logger.info("30日以内スキップ topic 数=%s", len(skip_ids))

        item = pick_topic(skip_topic_ids=skip_ids, preferred_category=preferred)
        item = attach_affiliate_urls(item)
        article_url = build_cushion_page_url(pages_base, item.topic_id)
        logger.info("選定 topic_id=%s / URL=%s", item.topic_id, article_url)

        if args.offline:
            from modules.ai_generator import _fallback_article_html, _fallback_x_post_text

            article_html = _fallback_article_html(item)
            tweet_text = _fallback_x_post_text(item, article_url=article_url)
        else:
            from modules.ai_generator import (
                create_gemini_client,
                generate_article_html,
                generate_x_post_text,
            )

            gemini_key = require_env("GEMINI_API_KEY")
            gemini_client = create_gemini_client(gemini_key)
            article_html = generate_article_html(gemini_client, item)
            tweet_text = generate_x_post_text(
                gemini_client,
                item,
                cushion_page_url=article_url,
            )

        card_summary = extract_card_summary(article_html, item)
        logger.info("生成ツイート:\n%s", tweet_text)

        write_article_and_update_index(
            item,
            article_html,
            github_pages_base_url=pages_base,
            summary=card_summary,
        )

        if args.dry_run:
            logger.info("dry-run: X 投稿と履歴更新をスキップしました。")
            return 0

        x_api_key = os.getenv("X_API_KEY", "").strip()
        x_api_secret = os.getenv("X_API_SECRET", "").strip()
        x_access_token = os.getenv("X_ACCESS_TOKEN", "").strip()
        x_access_secret = os.getenv("X_ACCESS_SECRET", "").strip()
        if not all([x_api_key, x_api_secret, x_access_token, x_access_secret]):
            raise RuntimeError("X API 認証情報が不足しています（ページのみなら --dry-run）。")

        from modules.x_poster import post_to_x

        post_to_x(
            tweet_text,
            api_key=x_api_key,
            api_secret=x_api_secret,
            access_token=x_access_token,
            access_secret=x_access_secret,
            skip_sleep=args.skip_x_sleep,
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        history = [h for h in history if str(h.get("topic_id")) != item.topic_id]
        history.append(
            {
                "topic_id": item.topic_id,
                "posted_at": now_iso,
                "category": item.category,
                "cushion_url": article_url,
            }
        )
        save_posted_history(posted_path, history)
        logger.info("posted.json を更新しました topic_id=%s", item.topic_id)
        return 0

    except Exception as exc:
        logger.exception("処理中にエラーが発生しました: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
