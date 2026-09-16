# ==========================================
# Version: 4.0.0
# Date: 2026-09-16
# Summary: 売れ筋商品起点のアフィリエイト記事生成に切替
# ==========================================
"""
Amazon / 楽天 / メルカリの売れ筋から商品を取得し、
商品ページ直URL付き記事を生成して index 更新 → X 投稿。

件数ルール:
- 各ショップの上位 RANKING_POOL(20) を見る
- 1回の実行で各 PUBLISH_PER_SOURCE(5) 件まで記事化（最大15件）
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

from gemini_helper import analyze_product_with_gemini
from product_fetcher import (
    PUBLISH_PER_SOURCE,
    RANKING_POOL,
    fetch_all_marketplace_products,
)
from site_builder import article_public_url, load_entries, publish_article

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
    )
    try:
        response = client.create_tweet(text=text)
    except HTTPException as exc:
        raise RuntimeError(f"X API error: {exc}") from exc
    tweet_id = ""
    if response and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError("X 投稿IDを取得できませんでした。")
    return tweet_id


def build_tweet(base: str, *, article_url: str) -> str:
    body = (base or "").strip()
    if article_url and article_url not in body:
        body = f"{body}\n{article_url}".strip()
    return body[:280]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trend Pick product affiliate bot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-x", action="store_true")
    parser.add_argument(
        "--per-source",
        type=int,
        default=PUBLISH_PER_SOURCE,
        help=f"各ショップから記事化する件数（既定 {PUBLISH_PER_SOURCE}）",
    )
    parser.add_argument(
        "--pool",
        type=int,
        default=RANKING_POOL,
        help=f"ランキングから見る件数（既定 {RANKING_POOL}）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="全体の最大記事化件数（0=制限なし、per-source*3まで）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = project_root()
    os.chdir(root)
    load_dotenv(root / ".env")

    try:
        require_env("GEMINI_API_KEY")
        require_env("AMAZON_ASSOCIATE_TAG")
        require_env("RAKUTEN_AF_ID")
        require_env("MERCARI_AFID")

        posted_path = root / POSTED_JSON
        history = load_posted(posted_path)
        skip_ids = {
            str(p.get("product_id", "")).strip()
            for p in history
            if str(p.get("product_id", "")).strip()
        }
        # 既存記事の商品URLもスキップ
        for entry in load_entries():
            src = (entry.source_link or "").strip()
            if "/dp/" in src:
                asin = src.split("/dp/")[-1].split("?")[0].split("/")[0]
                if asin:
                    skip_ids.add(f"amazon:{asin}")
            if "item.rakuten.co.jp" in src or "rakuten.co.jp" in src:
                skip_ids.add(f"rakuten:{src}")
            for key, url in (entry.links or {}).items():
                if key == "amazon" and "/dp/" in url:
                    asin = url.split("/dp/")[-1].split("?")[0].split("/")[0]
                    skip_ids.add(f"amazon:{asin}")
                if key == "rakuten":
                    skip_ids.add(f"rakuten:{url}")
                    # affiliate URL 内の素の商品URLも
                    if "item.rakuten.co.jp" in url:
                        skip_ids.add(f"rakuten:{url}")

        products = fetch_all_marketplace_products(
            pool=max(1, args.pool),
            per_source=max(1, args.per_source),
            skip_ids=skip_ids,
        )
        if args.limit and args.limit > 0:
            products = products[: args.limit]
        if not products:
            raise RuntimeError("紹介可能な新着商品が見つかりませんでした。")

        logger.info(
            "取得商品 %s 件（pool=%s / per_source=%s）",
            len(products),
            args.pool,
            args.per_source,
        )

        published = 0
        for product in products:
            analyzed = analyze_product_with_gemini(product)
            links = {product.source: product.url}
            entry = publish_article(
                source_link=product.url,
                title=str(analyzed["article_title"]),
                body=str(analyzed["article_body"]),
                has_product_links=True,
                keyword=str(analyzed["keyword"]) or product.keyword,
                links=links,
                badge=product.badge,
            )
            article_url = article_public_url(entry.article_id)
            final_tweet = build_tweet(
                str(analyzed["tweet_text"]), article_url=article_url
            )
            logger.info("article=%s source=%s", article_url, product.source)

            if args.dry_run:
                logger.info("dry-run tweet:\n%s", final_tweet)
                published += 1
                continue

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
                    "product_id": product.product_id,
                    "source": product.source,
                    "title": product.title,
                    "url": product.url,
                    "article_id": entry.article_id,
                    "article_url": article_url,
                    "tweet_id": tweet_id,
                    "x_error": x_error,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            # 重複防止のため都度保存
            save_posted(posted_path, history[-300:])
            published += 1

        if args.dry_run:
            logger.info("dry-run: %s 件の記事化シミュレーション完了", published)
            return 0

        logger.info("完了: %s 件の商品記事を公開", published)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("失敗: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
