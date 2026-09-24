# ==========================================
# Version: 6.0.0
# Date: 2026-09-24
# Summary: X投稿をブラウザ既定に変更（API課金回避）
# ==========================================
"""
Amazon / 楽天 / メルカリの売れ筋から商品を取得し、
商品ページ直URL付き記事を生成して index 更新 → X スレッド投稿。

件数ルール:
- 各ショップの上位 RANKING_POOL(20) を見る
- 1回の実行で各 PUBLISH_PER_SOURCE(5) 件まで記事化（最大15件）
- 主ショップは商品直URL、他ショップは商品名検索アフィ
- X は既定でブラウザ投稿（X_POST_METHOD=browser）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

from gemini_helper import analyze_product_with_gemini
from modules.x_browser_poster import post_to_x_via_browser
from product_fetcher import (
    PUBLISH_PER_SOURCE,
    RANKING_POOL,
    fetch_all_marketplace_products,
    is_usable_product_title,
)
from site_builder import article_public_url, load_entries, publish_article
from url_generator import build_cross_shop_links

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


def x_post_method() -> str:
    """投稿方式: browser（既定）または api。"""
    raw = os.getenv("X_POST_METHOD", "browser").strip().lower()
    if raw in {"api", "twitter_api", "x_api"}:
        return "api"
    return "browser"


def browser_headless() -> bool:
    """ブラウザ投稿をヘッドレスにするか。"""
    return os.getenv("X_BROWSER_HEADLESS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _x_client():
    import tweepy

    return tweepy.Client(
        consumer_key=require_env("X_API_KEY"),
        consumer_secret=require_env("X_API_SECRET"),
        access_token=require_env("X_ACCESS_TOKEN"),
        access_token_secret=require_env("X_ACCESS_SECRET"),
    )


def _tweet_id_from_response(response: object) -> str:
    tweet_id = ""
    if response and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError("X 投稿IDを取得できませんでした。")
    return tweet_id


def post_thread_to_x_api(*, main_text: str, reply_text: str) -> tuple[str, str]:
    """API で本投稿→リプライ。戻り値は (本投稿ID, リプライID)。"""
    from tweepy.errors import HTTPException

    client = _x_client()
    try:
        main_res = client.create_tweet(text=main_text)
        main_id = _tweet_id_from_response(main_res)
        reply_res = client.create_tweet(
            text=reply_text,
            in_reply_to_tweet_id=main_id,
        )
        reply_id = _tweet_id_from_response(reply_res)
    except HTTPException as exc:
        raise RuntimeError(f"X API error: {exc}") from exc
    return main_id, reply_id


def dispatch_x_post(*, main_text: str, reply_text: str) -> tuple[str, str]:
    """設定に応じてブラウザまたは API で X 投稿する。"""
    method = x_post_method()
    if method == "browser":
        logger.info("X 投稿方式: browser")
        return post_to_x_via_browser(
            main_text,
            reply_text,
            headless=browser_headless(),
        )

    logger.info("X 投稿方式: api")
    return post_thread_to_x_api(main_text=main_text, reply_text=reply_text)


def build_main_tweet(base: str) -> str:
    """本投稿。URLは入れない。"""
    body = (base or "").strip()
    body = re_strip_urls(body)
    return body[:270]


def build_reply_tweet(*, article_url: str) -> str:
    """リプライ。記事URLのみ。"""
    url = (article_url or "").strip()
    if not url:
        return "探したリンクは記事にまとめてあります。"
    return f"探したリンクまとめ↓\n{url}"[:280]


def re_strip_urls(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", "", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_rakuten_item_url(url: str) -> str:
    text = unquote(url or "")
    marker = "item.rakuten.co.jp"
    if marker not in text:
        return ""
    start = text.find("https://item.rakuten.co.jp")
    if start < 0:
        start = text.find("http://item.rakuten.co.jp")
    if start < 0:
        return ""
    end = start
    while end < len(text) and text[end] not in "&\"' <>":
        end += 1
    return text[start:end].rstrip("/")


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
        require_env("SURUGAYA_USER_ID")

        posted_path = root / POSTED_JSON
        history = load_posted(posted_path)
        skip_ids = {
            str(p.get("product_id", "")).strip()
            for p in history
            if str(p.get("product_id", "")).strip()
        }
        for entry in load_entries():
            src = (entry.source_link or "").strip()
            if "/dp/" in src:
                asin = src.split("/dp/")[-1].split("?")[0].split("/")[0]
                if asin:
                    skip_ids.add(f"amazon:{asin}")
            raw_rk = _extract_rakuten_item_url(src)
            if raw_rk:
                skip_ids.add(f"rakuten:{raw_rk}")
            for key, url in (entry.links or {}).items():
                if key == "amazon" and "/dp/" in url:
                    asin = url.split("/dp/")[-1].split("?")[0].split("/")[0]
                    skip_ids.add(f"amazon:{asin}")
                if key == "rakuten":
                    raw = _extract_rakuten_item_url(url)
                    if raw:
                        skip_ids.add(f"rakuten:{raw}")

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
            if not is_usable_product_title(product.title):
                logger.warning("ゴミタイトルのためスキップ: %s", product.title[:60])
                continue

            analyzed = analyze_product_with_gemini(product)
            article_title = str(analyzed["article_title"])
            if not is_usable_product_title(article_title) or any(
                ng in article_title for ng in ("管理番号", "商品番号", "フィギュア（楽天）")
            ):
                logger.warning("生成タイトルがゴミのためスキップ: %s", article_title[:60])
                continue

            links = build_cross_shop_links(
                primary_source=product.source,
                primary_url=product.url,
                product_name=str(analyzed["keyword"]) or product.title,
            )
            entry = publish_article(
                source_link=product.url,
                title=article_title,
                body=str(analyzed["article_body"]),
                has_product_links=True,
                keyword=str(analyzed["keyword"]) or product.keyword,
                links=links,
                badge=product.badge,
                image_url=getattr(product, "image_url", "") or "",
            )
            article_url = article_public_url(entry.article_id)
            main_tweet = build_main_tweet(str(analyzed["tweet_text"]))
            reply_tweet = build_reply_tweet(article_url=article_url)
            logger.info(
                "article=%s source=%s links=%s",
                article_url,
                product.source,
                list(links),
            )

            if args.dry_run:
                logger.info("dry-run main tweet:\n%s", main_tweet)
                logger.info("dry-run reply tweet:\n%s", reply_tweet)
                published += 1
                continue

            tweet_id = ""
            reply_tweet_id = ""
            x_error = ""
            x_method = x_post_method()
            if args.skip_x:
                logger.warning("--skip-x のため X 投稿をスキップ")
            else:
                try:
                    tweet_id, reply_tweet_id = dispatch_x_post(
                        main_text=main_tweet,
                        reply_text=reply_tweet,
                    )
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
                    "reply_tweet_id": reply_tweet_id,
                    "x_method": x_method if not args.skip_x else "skipped",
                    "x_error": x_error,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            save_posted(posted_path, history[-300:])
            published += 1

        if args.dry_run:
            logger.info("dry-run: %s 件の記事化シミュレーション完了", published)
            return 0

        if published == 0:
            raise RuntimeError("公開可能な商品記事が0件でした。")

        logger.info("完了: %s 件の商品記事を公開", published)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("失敗: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
