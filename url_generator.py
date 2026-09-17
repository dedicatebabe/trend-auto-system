# ==========================================
# Version: 2.2.0
# Date: 2026-09-17
# Summary: Yahoo!ショッピング検索リンクを追加しCTA用3店を整備
# ==========================================
"""キーワード／商品ページから各ショップのアフィリエイトURLを組み立てる。"""

from __future__ import annotations

import os
import re
from urllib.parse import quote


def generate_mercari_url(keyword: str, *, afid: str | None = None) -> str:
    """メルカリ検索アフィリエイトURL。"""
    afid_value = (afid if afid is not None else os.getenv("MERCARI_AFID", "")).strip()
    if not afid_value:
        raise RuntimeError("環境変数 MERCARI_AFID が設定されていません。")
    encoded = quote(keyword.strip(), safe="")
    return (
        f"https://jp.mercari.com/search?keyword={encoded}"
        f"&status=on_sale&afid={quote(afid_value, safe='')}"
    )


def generate_surugaya_url(keyword: str, *, user_id: str | None = None) -> str:
    """駿河屋アフィリエイトジャンプURL。"""
    uid = (user_id if user_id is not None else os.getenv("SURUGAYA_USER_ID", "")).strip()
    if not uid:
        raise RuntimeError("環境変数 SURUGAYA_USER_ID が設定されていません。")
    encoded_kw = quote(keyword.strip(), safe="")
    target = f"https://www.suruga-ya.jp/search?search_word={encoded_kw}"
    encoded_target = quote(target, safe="")
    return (
        "https://affiliate.suruga-ya.jp/modules/af/af_jump.php"
        f"?user_id={quote(uid, safe='')}"
        f"&action=default&url={encoded_target}"
    )


def generate_amazon_url(keyword: str, *, tag: str | None = None) -> str:
    """Amazon.co.jp 検索アフィリエイトURL。"""
    associate_tag = (
        tag if tag is not None else os.getenv("AMAZON_ASSOCIATE_TAG", "")
    ).strip()
    if not associate_tag:
        raise RuntimeError("環境変数 AMAZON_ASSOCIATE_TAG が設定されていません。")
    encoded = quote(keyword.strip(), safe="")
    return (
        f"https://www.amazon.co.jp/s?k={encoded}"
        f"&tag={quote(associate_tag, safe='')}"
    )


def generate_rakuten_url(keyword: str, *, af_id: str | None = None) -> str:
    """楽天市場検索のアフィリエイトURL。"""
    rid = (af_id if af_id is not None else os.getenv("RAKUTEN_AF_ID", "")).strip()
    if not rid:
        raise RuntimeError("環境変数 RAKUTEN_AF_ID が設定されていません。")
    encoded_kw = quote(keyword.strip(), safe="")
    search_url = f"https://search.rakuten.co.jp/search/mall/{encoded_kw}/"
    return (
        f"https://hb.afl.rakuten.co.jp/ichiba/{rid}/"
        f"?pc={quote(search_url, safe='')}"
        f"&link_type=text&id=0"
    )


def generate_yahoo_url(keyword: str, *, sid: str | None = None) -> str:
    """
    Yahoo!ショッピング検索URL。

    YAHOO_SHOPPING_SID があれば vc_sid 付き、なければ通常検索。
    """
    encoded = quote(keyword.strip(), safe="")
    base = f"https://shopping.yahoo.co.jp/search?p={encoded}"
    sid_value = (sid if sid is not None else os.getenv("YAHOO_SHOPPING_SID", "")).strip()
    if sid_value:
        return f"{base}&sc_i={quote(sid_value, safe='')}"
    return base


def generate_affiliate_urls(keyword: str) -> dict[str, str]:
    """
    商品名検索向けリンク一式を返す。

    戻り値キー: amazon, rakuten, yahoo, mercari, surugaya
    """
    if not keyword or not keyword.strip():
        raise ValueError("keyword が空です。")
    return {
        "amazon": generate_amazon_url(keyword),
        "rakuten": generate_rakuten_url(keyword),
        "yahoo": generate_yahoo_url(keyword),
        "mercari": generate_mercari_url(keyword),
        "surugaya": generate_surugaya_url(keyword),
    }


def clean_search_keyword(name: str) -> str:
    """他店検索用に商品名を短く整える。"""
    text = (name or "").strip()
    text = re.sub(r"[【】\[\]（）()『』「」]", " ", text)
    text = re.sub(r"(管理番号|商品番号|品番)[:：]?\s*[A-Za-z0-9\-]+", " ", text)
    text = re.sub(r"\bB0[A-Z0-9]{8}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 48:
        text = text[:48].rsplit(" ", 1)[0] or text[:48]
    return text


def build_cross_shop_links(
    *,
    primary_source: str,
    primary_url: str,
    product_name: str,
) -> dict[str, str]:
    """
    主ショップは商品直URL、他ショップは商品名検索アフィ。

    例: Amazon商品なら amazon=直URL、楽天/Yahoo/メルカリ/駿河屋=商品名検索
    """
    keyword = clean_search_keyword(product_name)
    if not keyword:
        raise ValueError("検索用の商品名が空です。")
    links = generate_affiliate_urls(keyword)
    source = (primary_source or "").strip().lower()
    if source in links and primary_url.strip():
        links[source] = primary_url.strip()
    return links
