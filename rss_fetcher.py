# ==========================================
# Version: 1.1.0
# Date: 2026-09-14
# Summary: 404フィード除外とスポーツ系ノイズ除外を追加
# ==========================================
"""PR TIMES RSS から最新の関連ニュースを1件取得する。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

import feedparser
import requests

logger = logging.getLogger(__name__)

DEFAULT_FEED_URLS = (
    # カテゴリ別は現状404のため、総合フィードを利用
    "https://prtimes.jp/index.rdf",
)

RELEVANT_KEYWORDS = (
    "アニメ",
    "漫画",
    "マンガ",
    "コミック",
    "ゲーム",
    "フィギュア",
    "ホビー",
    "キャラクター",
    "声優",
    "Blu-ray",
    "ブルーレイ",
    "Nintendo",
    "ニンテンドー",
    "PlayStation",
    "プレイステーション",
    "Xbox",
    "Steam",
    "ガジェット",
    "ラノベ",
    "ライトノベル",
    "バンダイ",
    "コトブキヤ",
    "グッドスマイル",
    "プライズ",
    "一番くじ",
    "フリューくじ",
)

EXCLUDE_KEYWORDS = (
    "B.LEAGUE",
    "Jリーグ",
    "プロ野球",
    "NPB",
    "スポンサー契約",
    "ユニフォーム",
    "サッカー",
    "バスケット",
    "野球",
    "ゴルフ",
)


@dataclass(frozen=True)
class NewsItem:
    """RSS から取得したニュース1件。"""

    link: str
    title: str
    summary: str
    published: str = ""
    source_feed: str = ""


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_relevant(title: str, summary: str) -> bool:
    blob = f"{title}\n{summary}"
    if any(k.lower() in blob.lower() for k in EXCLUDE_KEYWORDS):
        return False
    return any(k.lower() in blob.lower() for k in RELEVANT_KEYWORDS)


def _is_category_feed(url: str) -> bool:
    return any(seg in url for seg in ("/anime/", "/tv/", "/hobby/", "/game/"))


def _fetch_feed_xml(url: str, *, timeout: int = 20) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "TrendPickBot/1.0 (+https://dedicatebabe.github.io/trend-auto-system/)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def parse_feed_entries(feed_url: str) -> list[NewsItem]:
    """単一フィードをパースして NewsItem 一覧を返す。"""
    try:
        xml_text = _fetch_feed_xml(feed_url)
    except requests.RequestException as exc:
        logger.warning("RSS 取得失敗: %s (%s)", feed_url, exc)
        return []

    parsed = feedparser.parse(xml_text)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        logger.warning(
            "RSS パース失敗: %s (%s)",
            feed_url,
            getattr(parsed, "bozo_exception", ""),
        )
        return []

    items: list[NewsItem] = []
    for entry in parsed.entries:
        link = str(getattr(entry, "link", "") or "").strip()
        title = _strip_html(str(getattr(entry, "title", "") or ""))
        summary = _strip_html(
            str(
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
                or ""
            )
        )
        published = str(
            getattr(entry, "published", "")
            or getattr(entry, "updated", "")
            or ""
        ).strip()
        if not link or not title:
            continue
        items.append(
            NewsItem(
                link=link,
                title=title,
                summary=summary,
                published=published,
                source_feed=feed_url,
            )
        )
    return items


def fetch_latest_news(
    *,
    skip_links: Iterable[str] | None = None,
    feed_urls: Iterable[str] | None = None,
) -> NewsItem:
    """
    関連ニュースの最新1件を返す。

    すでに投稿済みの link はスキップする。
    見つからない場合は RuntimeError。
    """
    skipped = {str(x).strip() for x in (skip_links or []) if str(x).strip()}
    urls = tuple(feed_urls) if feed_urls else DEFAULT_FEED_URLS

    collected: list[NewsItem] = []
    for url in urls:
        entries = parse_feed_entries(url)
        logger.info("RSS %s: %s 件", url, len(entries))
        collected.extend(entries)

    seen_links: set[str] = set()
    unique: list[NewsItem] = []
    for item in collected:
        if item.link in seen_links:
            continue
        seen_links.add(item.link)
        unique.append(item)

    for item in unique:
        if item.link in skipped:
            logger.info("投稿済みのためスキップ: %s", item.link)
            continue
        # カテゴリ別フィードはそのまま採用、総合フィードはキーワードで絞る
        if not _is_category_feed(item.source_feed):
            if not _is_relevant(item.title, item.summary):
                continue
        logger.info("採用ニュース: %s", item.title)
        return item

    raise RuntimeError("投稿可能な未処理ニュースが見つかりませんでした。")
