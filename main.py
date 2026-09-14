# ==========================================
# Version: 2.1.0
# Date: 2026-09-14
# Summary: X投稿失敗時も記事反映を残し、Gemini移行に追従
# ==========================================
"""
PR TIMES RSS から話題を取得し、Gemini で解析、
docs/index.html 更新と X 投稿を行うメインスクリプト。
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
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
from url_generator import generate_affiliate_urls

SITE_URL = "https://dedicatebabe.github.io/trend-auto-system/"
POSTED_JSON = "posted.json"
INDEX_PATH = Path("docs") / "index.html"
CARD_START = "<!-- TREND_CARDS_START -->"
CARD_END = "<!-- TREND_CARDS_END -->"

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


def build_card_html(
    *,
    article_title: str,
    article_body: str,
    mercari_url: str,
    surugaya_url: str,
    source_link: str,
) -> str:
    """index.html に挿入するカード HTML。"""
    title = html.escape(article_title)
    body = html.escape(article_body)
    # カードでは冒頭だけ表示
    excerpt = body if len(body) <= 160 else body[:157] + "..."
    mercari = html.escape(mercari_url, quote=True)
    surugaya = html.escape(surugaya_url, quote=True)
    source = html.escape(source_link, quote=True)
    return f"""
      <article class="card trend-card">
        <div class="card-media media-anime"><span>TREND</span></div>
        <div class="card-body">
          <span class="badge">PR TIMES</span>
          <h3>{title}</h3>
          <p>{excerpt}</p>
          <div class="card-actions">
            <a class="btn btn-mercari" href="{mercari}" rel="nofollow sponsored noopener" target="_blank">メルカリで探す</a>
            <a class="btn btn-surugaya" href="{surugaya}" rel="nofollow sponsored noopener" target="_blank">駿河屋で探す</a>
          </div>
          <p class="source"><a href="{source}" rel="noopener" target="_blank">元記事を見る</a></p>
        </div>
      </article>
"""


def ensure_card_markers(index_html: str) -> str:
    """カード挿入マーカーが無ければ card-grid 内に追加する。"""
    if CARD_START in index_html and CARD_END in index_html:
        return index_html
    pattern = re.compile(r'(<div class="card-grid">\s*)', flags=re.IGNORECASE)
    match = pattern.search(index_html)
    if not match:
        raise RuntimeError("docs/index.html に card-grid が見つかりません。")
    insert_at = match.end()
    markers = f"{CARD_START}\n{CARD_END}\n"
    return index_html[:insert_at] + markers + index_html[insert_at:]


def prepend_card_to_index(card_html: str, *, index_path: Path) -> None:
    """新しいカードを card-grid 先頭へ追記する。"""
    raw = index_path.read_text(encoding="utf-8")
    raw = ensure_card_markers(raw)
    start = raw.find(CARD_START)
    end = raw.find(CARD_END)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError("カード挿入マーカーの位置が不正です。")
    inner_start = start + len(CARD_START)
    existing = raw[inner_start:end]
    updated_inner = "\n" + card_html + existing
    new_html = raw[:inner_start] + updated_inner + raw[end:]
    # バージョンヘッダーを軽く更新
    new_html = re.sub(
        r"(Version:\s*)([\d.]+)",
        lambda m: f"{m.group(1)}{m.group(2)}",
        new_html,
        count=1,
    )
    index_path.write_text(new_html, encoding="utf-8")
    logger.info("docs/index.html にカードを追記しました")


def ensure_action_styles(index_path: Path) -> None:
    """メルカリ／駿河屋ボタン用 CSS が無ければ追記する。"""
    raw = index_path.read_text(encoding="utf-8")
    if ".btn-mercari" in raw:
        return
    css = """
    .card-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.5rem;
      margin-top: 0.9rem;
    }
    .btn {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0.45rem 0.6rem;
      border-radius: 8px;
      color: #fff !important;
      font-size: 0.8rem;
      font-weight: 700;
      text-align: center;
    }
    .btn-mercari { background: #ff333f; }
    .btn-surugaya { background: #0b1d36; }
    .source {
      margin: 0.7rem 0 0;
      font-size: 0.78rem;
    }
    .source a { color: var(--accent); text-decoration: underline; }
"""
    raw = raw.replace("</style>", css + "  </style>", 1)
    index_path.write_text(raw, encoding="utf-8")


def post_to_x(text: str) -> str:
    """tweepy で X に投稿し、tweet id を返す。"""
    import tweepy
    from tweepy.errors import HTTPException

    api_key = require_env("X_API_KEY")
    api_secret = require_env("X_API_SECRET")
    access_token = require_env("X_ACCESS_TOKEN")
    access_secret = require_env("X_ACCESS_SECRET")
    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
        wait_on_rate_limit=True,
    )
    try:
        response = client.create_tweet(text=text)
    except HTTPException as exc:
        # 402 credits depleted など課金・枠不足は致命扱いにせず上位で継続可能にする
        raise RuntimeError(f"X 投稿失敗: {exc}") from exc
    tweet_id = ""
    if response is not None and getattr(response, "data", None):
        tweet_id = str(response.data.get("id", ""))
    if not tweet_id:
        raise RuntimeError(f"X 投稿に失敗しました: {response}")
    logger.info("X 投稿成功 tweet_id=%s", tweet_id)
    return tweet_id


def build_tweet(tweet_text: str, *, site_url: str = SITE_URL) -> str:
    body = (tweet_text or "").strip()
    if site_url not in body:
        body = f"{body}\n{site_url}"
    return body[:280]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trend Pick PR TIMES bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="X 投稿と posted.json 更新をスキップ（index 更新は実行）",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="index.html 更新をスキップ",
    )
    parser.add_argument(
        "--skip-x",
        action="store_true",
        help="X 投稿だけスキップ（記事反映と履歴更新は行う）",
    )
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
        keyword = analyzed["keyword"]
        article_title = analyzed["article_title"]
        article_body = analyzed["article_body"]
        tweet_text = analyzed["tweet_text"]
        logger.info("keyword=%s", keyword)

        mercari_url, surugaya_url = generate_affiliate_urls(keyword)
        logger.info("mercari=%s", mercari_url)
        logger.info("surugaya=%s", surugaya_url)

        index_path = root / INDEX_PATH
        if not args.skip_index:
            ensure_action_styles(index_path)
            card = build_card_html(
                article_title=article_title,
                article_body=article_body,
                mercari_url=mercari_url,
                surugaya_url=surugaya_url,
                source_link=news.link,
            )
            prepend_card_to_index(card, index_path=index_path)

        final_tweet = build_tweet(tweet_text, site_url=SITE_URL)
        logger.info("tweet:\n%s", final_tweet)

        if args.dry_run:
            logger.info("dry-run: X 投稿と履歴更新をスキップ")
            return 0

        tweet_id = ""
        x_error = ""
        if args.skip_x:
            logger.warning("--skip-x のため X 投稿をスキップしました")
        else:
            try:
                tweet_id = post_to_x(final_tweet)
            except Exception as exc:  # noqa: BLE001
                # クレジット枯渇(402)などでも index/posted は残す
                x_error = str(exc)
                logger.error("X 投稿失敗（記事反映は継続）: %s", exc)

        history.append(
            {
                "link": news.link,
                "title": news.title,
                "keyword": keyword,
                "article_title": article_title,
                "tweet_id": tweet_id,
                "x_error": x_error,
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "mercari_url": mercari_url,
                "surugaya_url": surugaya_url,
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