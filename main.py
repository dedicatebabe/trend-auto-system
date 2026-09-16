# ==========================================
# Version: 3.0.0
# Date: 2026-09-16
# Summary: 個別記事型メディア＋商品リンク出し分けに刷新
# ==========================================
"""
PR TIMES RSS → Gemini 解析 → 個別記事生成 → index 更新 → X 投稿。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

from gemini_helper import analyze_news_with_gemini
from rss_fetcher import fetch_latest_news
from site_builder import article_public_url, publish_article
from url_generator import generate_affiliate_urls

SITE_URL = "https://dedicatebabe.github.io/trend-auto-system/"
POSTED_JSON = "posted.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trend-auto-system")


def project_root() -> Path:
    return Path(__file__).resolve().parent


def load_posted(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("posted.json 読み込み失敗: %s", exc)
        return []
    posts = data.get("posts", [])
    return [p for p in posts if isinstance(p, dict)] if isinstance(posts, list) else []


def save_posted(path: Path, posts: list[dict]) -> None:
    path.write_text(
        json.dumps({"posts": posts}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return value


def post_to_x(text: str) -> str:
    import tweepy
    from tweepy.errors import HTTPException

    client = tweepy.Client(
        consumer_key=require_env("X_API_KEY"),
        consumer_secret=require_env("X_API_SECRET"),
        access_token=require_env("X_ACCESS_TOKEN"),
        access_token_secret=require_env("X_ACCESS_SECRET"),
        wait_on_rate_limit=True,
    )
    try:
        response = client.create_tweet(text=text)
    except HTTPException as exc:
        raise RuntimeError(f"X 投稿失敗: {exc}") from exc
    tweet_id = ""
    if response is not None and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError(f"X 投稿に失敗しました: {response}")
    logger.info("X 投稿成功 tweet_id=%s", tweet_id)
    return tweet_id


def build_tweet(tweet_text: str, *, article_url: str) -> str:
    body = (tweet_text or "").strip()
    if article_url not in body:
        body = f"{body}\n{article_url}"
    return body[:280]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trend Pick PR TIMES bot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-x", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    os.chdir(root)
    load_dotenv(root / ".env")

    try:
        require_env("GEMINI_API_KEY")
        require_env("MERCARI_AFID")
        require_env("SURUGAYA_USER_ID")
        require_env("AMAZON_ASSOCIATE_TAG")
        require_env("RAKUTEN_AF_ID")

        posted_path = root / POSTED_JSON
        history = load_posted(posted_path)
        skip_links = {
            str(p.get("link", "")).strip()
            for p in history
            if str(p.get("link", "")).strip()
        }

        news = fetch_latest_news(skip_links=skip_links)
        logger.info("取得: %s", news.title)

        analyzed = analyze_news_with_gemini(news)
        has_product_links = bool(analyzed.get("has_product_links"))
        keyword = str(analyzed["keyword"])
        article_title = str(analyzed["article_title"])
        article_body = str(analyzed["article_body"])
        tweet_text = str(analyzed["tweet_text"])
        logger.info("has_product_links=%s keyword=%s", has_product_links, keyword)

        links: dict[str, str] = {}
        if has_product_links:
            links = generate_affiliate_urls(keyword)
            logger.info("product links generated")
        else:
            logger.info("読み物記事のため商品リンクは付けません")

        entry = publish_article(
            source_link=news.link,
            title=article_title,
            body=article_body,
            has_product_links=has_product_links,
            keyword=keyword,
            links=links,
        )
        article_url = article_public_url(entry.article_id)
        final_tweet = build_tweet(tweet_text, article_url=article_url)
        logger.info("article=%s", article_url)
        logger.info("tweet:\n%s", final_tweet)

        if args.dry_run:
            logger.info("dry-run: X 投稿と履歴更新をスキップ")
            return 0

        tweet_id = ""
        x_error = ""
        if args.skip_x:
            logger.warning("--skip-x のため X 投稿をスキップ")
        else:
            try:
                tweet_id = post_to_x(final_tweet)
            except Exception as exc:  # noqa: BLE001
                x_error = str(exc)
                logger.error("X 投稿失敗（記事反映は継続）: %s", exc)

        history.append(
            {
                "link": news.link,
                "title": news.title,
                "keyword": keyword,
                "article_title": article_title,
                "article_id": entry.article_id,
                "article_url": article_url,
                "has_product_links": has_product_links,
                "tweet_id": tweet_id,
                "x_error": x_error,
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "links": links,
            }
        )
        history = history[-200:]
        save_posted(posted_path, history)
        logger.info("完了 tweet_id=%s", tweet_id or "(none)")
        return 0

    except Exception as exc:
        logger.exception("処理失敗: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
