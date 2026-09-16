# ==========================================
# Version: 2.0.0
# Date: 2026-09-16
# Summary: Amazon・楽天を追加し商品リンク出し分けに対応
# ==========================================
"""キーワードから各ショップのアフィリエイト／検索URLを組み立てる。"""

from __future__ import annotations

import os
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
    """
    楽天市場検索のアフィリエイトURL。

    形式例:
    https://hb.afl.rakuten.co.jp/ichiba/{RAKUTEN_AF_ID}/?pc={urlencoded search url}
    """
    rid = (af_id if af_id is not None else os.getenv("RAKUTEN_AF_ID", "")).strip()
    if not rid:
        raise RuntimeError("環境変数 RAKUTEN_AF_ID が設定されていません。")
    # 検索URLはパスにキーワードを入れる
    encoded_kw = quote(keyword.strip(), safe="")
    search_url = f"https://search.rakuten.co.jp/search/mall/{encoded_kw}/"
    return (
        f"https://hb.afl.rakuten.co.jp/ichiba/{rid}/"
        f"?pc={quote(search_url, safe='')}"
        f"&link_type=text&id=0"
    )


def generate_affiliate_urls(keyword: str) -> dict[str, str]:
    """
    商品向けリンク一式を返す。

    戻り値キー: amazon, rakuten, mercari, surugaya
    """
    if not keyword or not keyword.strip():
        raise ValueError("keyword が空です。")
    return {
        "amazon": generate_amazon_url(keyword),
        "rakuten": generate_rakuten_url(keyword),
        "mercari": generate_mercari_url(keyword),
        "surugaya": generate_surugaya_url(keyword),
    }
