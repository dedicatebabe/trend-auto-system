# ==========================================
# Version: 7.1.1
# Date: 2026-10-18
# Summary: 需要ベース選定の採用理由をログ出力
# ==========================================
"""
Amazon / 楽天 / メルカリの売れ筋から商品を取得し、
商品ページ直URL付き記事を生成して index 更新 → X スレッド投稿。

件数ルール:
- 各ショップの上位 RANKING_POOL を見る
- 1回の実行で各 PUBLISH_PER_SOURCE 件まで記事化
- 主ショップは商品直URL、他ショップは商品名検索アフィ
- X は既定でブラウザ投稿（X_POST_METHOD=browser）
- 親ポストに外部URLなし → 約60秒後リプライでクッション送客
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

from gemini_helper import analyze_product_with_gemini
from modules.media_prep import prepare_parent_images, prepare_reply_webp
from modules.x_browser_poster import (
    DEFAULT_REPLY_DELAY_SEC,
    post_to_x_via_browser,
)
from product_fetcher import (
    PUBLISH_PER_SOURCE,
    RANKING_POOL,
    fetch_all_marketplace_products,
    is_on_brand_product,
    is_usable_product_title,
    normalize_product_id,
    title_fingerprint,
)
from site_builder import article_public_url, load_entries, publish_article
from url_generator import build_cross_shop_links

SITE_URL = "https://dedicatebabe.github.io/trend-auto-system/"
POSTED_JSON = "posted.json"
# 同一商品の再投稿クールダウン（成功投稿はより長く）
REPOST_COOLDOWN_DAYS = 7
REPOST_SUCCESS_COOLDOWN_DAYS = 30
PARENT_FOOTER = "予約・在庫状況はリプライへ ↓"
JST = ZoneInfo("Asia/Tokyo")
# [start_hour, end_hour) in JST
ACTIVE_WINDOWS = ((7, 9), (12, 13), (16, 20))

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


def reply_delay_sec() -> float:
    """親→リプの待機秒数（既定60）。"""
    raw = os.getenv("X_REPLY_DELAY_SEC", str(int(DEFAULT_REPLY_DELAY_SEC))).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(DEFAULT_REPLY_DELAY_SEC)


def is_active_posting_hour(now: datetime | None = None) -> bool:
    """投稿アクティブ時間帯か（JST）。"""
    current = now or datetime.now(JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=JST)
    else:
        current = current.astimezone(JST)
    hour = current.hour
    return any(start <= hour < end for start, end in ACTIVE_WINDOWS)


def _parse_posted_at(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def recent_product_skip_ids(
    history: list[dict],
    *,
    days: float = REPOST_COOLDOWN_DAYS,
    success_days: float = REPOST_SUCCESS_COOLDOWN_DAYS,
) -> set[str]:
    """同一商品の再投稿クールダウン内の product_id 集合（正規化済み）。"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    success_cutoff = now - timedelta(days=success_days)
    skip: set[str] = set()
    for row in history:
        pid = normalize_product_id(str(row.get("product_id", "")))
        if not pid:
            continue
        posted_at = _parse_posted_at(row.get("posted_at"))
        if posted_at is None:
            skip.add(pid)
            continue
        has_tweet = bool(str(row.get("tweet_id", "")).strip())
        if has_tweet and posted_at >= success_cutoff:
            skip.add(pid)
        elif posted_at >= cutoff:
            skip.add(pid)
    return skip


def recent_title_fingerprints(
    history: list[dict],
    *,
    days: float = REPOST_SUCCESS_COOLDOWN_DAYS,
) -> set[str]:
    """直近投稿タイトルの指紋集合（表記ゆれの同一商品を抑止）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: set[str] = set()
    for row in history:
        fp = title_fingerprint(str(row.get("title", "")))
        if not fp:
            continue
        posted_at = _parse_posted_at(row.get("posted_at"))
        if posted_at is None or posted_at >= cutoff:
            out.add(fp)
    return out


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


def post_thread_to_x_api(
    *,
    parent_text: str,
    reply_text: str,
    delay_sec: float,
) -> tuple[str, str]:
    """API で親→待機→リプ。戻り値は (parent_id, reply_id)。"""
    from tweepy.errors import HTTPException

    client = _x_client()
    try:
        parent_resp = client.create_tweet(text=parent_text)
        parent_id = _tweet_id_from_response(parent_resp)
        if delay_sec > 0:
            logger.info("APIリプライまで %.0f 秒待機", delay_sec)
            time.sleep(delay_sec)
        reply_resp = client.create_tweet(
            text=reply_text,
            in_reply_to_tweet_id=parent_id,
        )
        reply_id = _tweet_id_from_response(reply_resp)
        return parent_id, reply_id
    except HTTPException as exc:
        raise RuntimeError(f"X API error: {exc}") from exc


def dispatch_x_thread(
    *,
    parent_text: str,
    reply_text: str,
    parent_image_paths: list[Path] | None = None,
    reply_image_path: Path | None = None,
) -> tuple[str, str]:
    """設定に応じてブラウザまたは API で親＋リプ投稿する。"""
    method = x_post_method()
    delay = reply_delay_sec()
    if method == "browser":
        logger.info("X 投稿方式: browser（親→%.0fs→リプ）", delay)
        return post_to_x_via_browser(
            parent_text,
            reply_text,
            local_image_paths=list(parent_image_paths or []),
            reply_image_path=reply_image_path,
            reply_delay_sec=delay,
            headless=browser_headless(),
        )

    logger.info("X 投稿方式: api（親→%.0fs→リプ・画像なし）", delay)
    return post_thread_to_x_api(
        parent_text=parent_text,
        reply_text=reply_text,
        delay_sec=delay,
    )


def re_strip_urls(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", "", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_parent_tweet(base: str) -> str:
    """親ポスト本文（外部URLなし・リプ誘導フッター付き）。"""
    body = re_strip_urls((base or "").strip())
    body = re.sub(
        r"(予約・在庫状況はリプライへ\s*↓?|詳細は(?:こちら|リプライへ).*)",
        "",
        body,
    ).strip()
    if "#PR" not in body and "#pr" not in body.lower():
        body = f"{body} #PR".strip()
    if PARENT_FOOTER not in body:
        body = f"{body}\n{PARENT_FOOTER}".strip()
    return body[:280]


def build_reply_tweet(*, article_url: str) -> str:
    """リプライ本文（クッションURL + #PR）。"""
    url = (article_url or "").strip()
    if not url:
        raise ValueError("article_url が空です。")
    text = f"{url}\n#PR"
    return text[:280]


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
        "--force",
        action="store_true",
        help="アクティブ時間外でも実行（同一商品の再投稿抑制は維持）",
    )
    parser.add_argument(
        "--allow-repost",
        action="store_true",
        help="同一商品の再投稿クールダウンを無視（検証用）",
    )
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
        if not args.force and not is_active_posting_hour():
            now = datetime.now(JST)
            logger.info(
                "アクティブ時間外のためスキップ（現在 %s JST）。"
                "枠: 07-09 / 12-13 / 16-20。手動は --force",
                now.strftime("%H:%M"),
            )
            return 0

        require_env("GEMINI_API_KEY")
        require_env("AMAZON_ASSOCIATE_TAG")
        require_env("RAKUTEN_AF_ID")
        require_env("MERCARI_AFID")
        require_env("SURUGAYA_USER_ID")

        posted_path = root / POSTED_JSON
        history = load_posted(posted_path)
        skip_ids: set[str] = set()
        skip_titles: set[str] = set()
        if args.allow_repost:
            logger.info("--allow-repost: 同一商品クールダウンを無視")
        else:
            skip_ids = recent_product_skip_ids(history)
            skip_titles = recent_title_fingerprints(history)
            if skip_ids or skip_titles:
                logger.info(
                    "同一商品スキップ: id=%s / title=%s（成功投稿%sdays / 通常%sdays）",
                    len(skip_ids),
                    len(skip_titles),
                    REPOST_SUCCESS_COOLDOWN_DAYS,
                    REPOST_COOLDOWN_DAYS,
                )

        for entry in load_entries():
            src = (entry.source_link or "").strip()
            if "/dp/" in src:
                asin = src.split("/dp/")[-1].split("?")[0].split("/")[0]
                if asin:
                    skip_ids.add(normalize_product_id(f"amazon:{asin}"))
            raw_rk = _extract_rakuten_item_url(src)
            if raw_rk:
                skip_ids.add(normalize_product_id(f"rakuten:{raw_rk}"))
            fp = title_fingerprint(entry.title)
            if fp:
                skip_titles.add(fp)
            for key, url in (entry.links or {}).items():
                if key == "amazon" and "/dp/" in url:
                    asin = url.split("/dp/")[-1].split("?")[0].split("/")[0]
                    skip_ids.add(normalize_product_id(f"amazon:{asin}"))
                if key == "rakuten":
                    raw = _extract_rakuten_item_url(url)
                    if raw:
                        skip_ids.add(normalize_product_id(f"rakuten:{raw}"))

        products = fetch_all_marketplace_products(
            pool=max(1, args.pool),
            per_source=max(1, args.per_source),
            skip_ids=skip_ids,
        )
        if args.limit and args.limit > 0:
            products = products[: args.limit]
        if not products:
            logger.warning("紹介可能な新着商品がありません。今回はスキップします。")
            return 0

        logger.info(
            "取得商品 %s 件（pool=%s / per_source=%s）",
            len(products),
            args.pool,
            args.per_source,
        )

        published = 0
        for product in products:
            if not is_usable_product_title(product.title) or not is_on_brand_product(
                product.title
            ):
                logger.warning("ブランド外/ゴミタイトルのためスキップ: %s", product.title[:60])
                continue

            pid = normalize_product_id(product.product_id)
            fp = title_fingerprint(product.title)
            if not args.allow_repost and (pid in skip_ids or (fp and fp in skip_titles)):
                logger.info("同一商品のためスキップ: %s", product.title[:60])
                continue

            extra = getattr(product, "extra", {}) or {}
            logger.info(
                "選定候補: score=%s cat=%s basis=%s reason=%s title=%s",
                extra.get("demand_score", product.score),
                extra.get("demand_category", ""),
                extra.get("demand_source", ""),
                str(extra.get("select_reason", ""))[:120],
                product.title[:60],
            )

            analyzed = analyze_product_with_gemini(product)
            article_title = str(analyzed["article_title"])
            if (
                not is_usable_product_title(article_title)
                or not is_on_brand_product(article_title)
                or any(
                    ng in article_title
                    for ng in ("管理番号", "商品番号", "フィギュア（楽天）")
                )
            ):
                logger.warning("生成タイトルが不適合のためスキップ: %s", article_title[:60])
                continue

            links = build_cross_shop_links(
                primary_source=product.source,
                primary_url=product.url,
                product_name=str(analyzed["keyword"]) or product.title,
            )
            image_url = getattr(product, "image_url", "") or ""
            price_val = getattr(product, "price", None)
            price_int = price_val if isinstance(price_val, int) else None
            entry = publish_article(
                source_link=product.url,
                title=article_title,
                body=str(analyzed["article_body"]),
                has_product_links=True,
                keyword=str(analyzed["keyword"]) or product.keyword,
                links=links,
                badge=product.badge,
                image_url=image_url,
                price=price_int,
                release_date="",
            )
            article_url = article_public_url(entry.article_id)
            parent_tweet = build_parent_tweet(str(analyzed["tweet_text"]))
            reply_tweet = build_reply_tweet(article_url=article_url)
            logger.info(
                "article=%s source=%s links=%s",
                article_url,
                product.source,
                list(links),
            )

            parent_paths = prepare_parent_images(
                [image_url] if image_url else [],
                max_count=4,
            )
            # 親に商品画像がある場合、リプへ同一画像を重ねると見た目が重複するので付けない。
            # 親が0枚のときだけリプに WebP サムネを付ける。
            reply_webp = None
            if (
                image_url
                and not parent_paths
                and not args.dry_run
                and not args.skip_x
            ):
                reply_webp = prepare_reply_webp(image_url)

            if args.dry_run:
                logger.info("dry-run parent:\n%s", parent_tweet)
                logger.info("dry-run reply:\n%s", reply_tweet)
                logger.info(
                    "dry-run media parent=%s reply_webp=%s",
                    len(parent_paths),
                    bool(reply_webp),
                )
                published += 1
                continue

            tweet_id = ""
            reply_id = ""
            x_error = ""
            x_method = x_post_method()
            if args.skip_x:
                logger.warning("--skip-x のため X 投稿をスキップ")
            else:
                try:
                    tweet_id, reply_id = dispatch_x_thread(
                        parent_text=parent_tweet,
                        reply_text=reply_tweet,
                        parent_image_paths=parent_paths,
                        reply_image_path=reply_webp,
                    )
                except Exception as exc:  # noqa: BLE001
                    x_error = str(exc)
                    logger.error("X 投稿失敗（記事反映は継続）: %s", exc)

            for path in parent_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if reply_webp and "docs/media" not in str(reply_webp):
                try:
                    reply_webp.unlink(missing_ok=True)
                except OSError:
                    pass

            history.append(
                {
                    "product_id": product.product_id,
                    "source": product.source,
                    "title": product.title,
                    "url": product.url,
                    "article_id": entry.article_id,
                    "article_url": article_url,
                    "tweet_id": tweet_id,
                    "reply_id": reply_id,
                    "x_method": x_method if not args.skip_x else "skipped",
                    "x_error": x_error,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            save_posted(posted_path, history[-300:])
            if pid:
                skip_ids.add(pid)
            if fp:
                skip_titles.add(fp)
            published += 1

        if args.dry_run:
            logger.info("dry-run: %s 件の記事化シミュレーション完了", published)
            return 0

        if published == 0:
            logger.warning("公開できた商品記事が0件でした。今回はスキップします。")
            return 0

        logger.info("完了: %s 件の商品記事を公開", published)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("失敗: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
