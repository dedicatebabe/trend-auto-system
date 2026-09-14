# ==========================================
# Version: 1.0.0
# Date: 2026-09-14
# Summary: メルカリ・駿河屋のアフィリエイト検索URLを生成
# ==========================================
"""キーワードからアフィリエイトURLを組み立てる。"""

from __future__ import annotations

import os
from urllib.parse import quote


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。")
    return value


def generate_mercari_url(keyword: str, *, afid: str | None = None) -> str:
    """
    メルカリ検索アフィリエイトURLを生成する。

    形式:
    https://jp.mercari.com/search?keyword={kw}&status=on_sale&afid={MERCARI_AFID}
    """
    afid_value = (afid if afid is not None else os.getenv("MERCARI_AFID", "")).strip()
    if not afid_value:
        raise RuntimeError("環境変数 MERCARI_AFID が設定されていません。")
    encoded = quote(keyword.strip(), safe="")
    return (
        f"https://jp.mercari.com/search?keyword={encoded}"
        f"&status=on_sale&afid={quote(afid_value, safe='')}"
    )


def generate_surugaya_url(keyword: str, *, user_id: str | None = None) -> str:
    """
    駿河屋アフィリエイトジャンプURLを生成する。

    形式:
    https://affiliate.suruga-ya.jp/modules/af/af_jump.php?user_id={id}
      &action=default&url=https%3A%2F%2Fwww.suruga-ya.jp%2Fsearch%3Fsearch_word%3D{kw}
    """
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


def generate_affiliate_urls(keyword: str) -> tuple[str, str]:
    """(メルカリURL, 駿河屋URL) を返す。"""
    if not keyword or not keyword.strip():
        raise ValueError("keyword が空です。")
    return generate_mercari_url(keyword), generate_surugaya_url(keyword)
