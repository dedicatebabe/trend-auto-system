# ==========================================
# Version: 1.0.0
# Date: 2026-09-13
# Summary: メルカリ・駿河屋の検索／アフィリエイトURL生成
# ==========================================
"""アフィリエイト／検索リンク組み立て。"""

from __future__ import annotations

import os
from urllib.parse import quote

from modules.topic_source import TrendItem


def build_mercari_url(keyword: str) -> str:
    """メルカリ検索URL（アフィリテンプレがあれば置換）。"""
    encoded = quote(keyword)
    template = os.getenv(
        "MERCARI_URL_TEMPLATE",
        "https://jp.mercari.com/search?keyword={keyword}",
    ).strip()
    return template.replace("{keyword}", encoded)


def build_surugaya_url(keyword: str) -> str:
    """駿河屋検索URL（アフィリテンプレがあれば置換）。"""
    encoded = quote(keyword)
    template = os.getenv(
        "SURUGAYA_URL_TEMPLATE",
        "https://www.suruga-ya.jp/search?category=&search_word={keyword}",
    ).strip()
    return template.replace("{keyword}", encoded)


def attach_affiliate_urls(item: TrendItem) -> TrendItem:
    """TrendItem にメルカリ／駿河屋URLを付与した新インスタンスを返す。"""
    return TrendItem(
        topic_id=item.topic_id,
        category=item.category,
        category_label=item.category_label,
        title=item.title,
        search_keyword=item.search_keyword,
        summary_hint=item.summary_hint,
        description=item.description,
        mercari_url=build_mercari_url(item.search_keyword),
        surugaya_url=build_surugaya_url(item.search_keyword),
        image_url=item.image_url,
    )
