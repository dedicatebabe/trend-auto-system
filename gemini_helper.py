# ==========================================
# Version: 2.0.0
# Date: 2026-09-16
# Summary: has_product_links 判定を追加し読み物と商品記事を分離
# ==========================================
"""Gemini API によるニュース解析ヘルパー。"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from rss_fetcher import NewsItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.6-flash"

SYSTEM_PROMPT = """あなたは日本のアニメ・ゲーム・ホビー・ガジェットの編集者です。
入力されたプレスリリース／ニュースを読み、次の JSON オブジェクトだけを出力してください。
アダルト・性的・過激な内容は禁止。一般向け（全年齢）のみ。

必須キー:
- has_product_links: boolean。読者が今すぐ商品・グッズを探せる話題なら true。
  true例: フィギュア、一番くじ、BD、ゲームソフト、ガジェット本体、公式グッズ発売
  false例: 企業のスポンサー契約、Forbesコラム、IR、イベント告知のみ、抽象的な読み物
- keyword: 検索用の短い語。has_product_links=false でも作品名・話題語を入れてよい
- article_title: Web記事用のキャッチーな日本語タイトル
- article_body: 見どころ文章。プレーンテキストのみ（HTML禁止）。200〜400字
- tweet_text: X投稿。フック＋詳細誘導。100文字前後

出力は JSON のみ。
"""


def _create_client(api_key: str | None = None) -> Any:
    from google import genai

    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("環境変数 GEMINI_API_KEY が設定されていません。")
    return genai.Client(api_key=key)


def _extract_response_text(response: Any) -> str:
    texts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "thought", False):
                continue
            part_text = getattr(part, "text", None)
            if part_text:
                texts.append(str(part_text))
    if texts:
        return "\n".join(texts).strip()
    try:
        return (response.text or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("Gemini 応答から JSON を抽出できませんでした。")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Gemini 応答 JSON がオブジェクトではありません。")
    return data


def _infer_has_product_links(news: NewsItem) -> bool:
    blob = f"{news.title}\n{news.summary}"
    negative = (
        "スポンサー",
        "富豪",
        "Forbes",
        "コラム",
        "決算",
        "IR",
        "業務提携",
        "就任",
        "開設",
        "寄付",
    )
    positive = (
        "フィギュア",
        "一番くじ",
        "くじ",
        "Blu-ray",
        "ブルーレイ",
        "発売",
        "予約",
        "グッズ",
        "アクリル",
        "ソフト",
        "本体",
        "イヤホン",
        "ガジェット",
    )
    if any(n in blob for n in negative):
        return False
    return any(p in blob for p in positive)


def _fallback_result(news: NewsItem) -> dict[str, Any]:
    """API 失敗時の安全なフォールバック。"""
    short_title = news.title[:40]
    keyword = re.sub(r"[【】「」\[\]（）()『』]", " ", short_title)
    keyword = re.sub(r"\s+", " ", keyword).strip()[:24] or "アニメ グッズ"
    has_links = _infer_has_product_links(news)
    body = (
        f"{news.title}\n\n"
        f"{(news.summary or '話題のリリースです。')[:280]}\n\n"
    )
    if has_links:
        body += "気になるアイテムは各ショップで在庫・価格を比較してみてください。"
    else:
        body += "まずは元の発表内容を確認するのがおすすめです。"
    tweet = (
        "これ気になる人多そう。\n"
        f"「{short_title}」のポイントをサクッとまとめました。詳細はこちら↓"
    )
    return {
        "has_product_links": has_links,
        "keyword": keyword,
        "article_title": f"【話題】{short_title}",
        "article_body": body,
        "tweet_text": tweet[:120],
    }


def analyze_news_with_gemini(
    news: NewsItem,
    *,
    api_key: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """
    ニュースを Gemini で解析し dict を返す。

    キー: has_product_links, keyword, article_title, article_body, tweet_text
    """
    from google.genai import types

    client = _create_client(api_key)
    model_id = (model_name or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)).strip()
    user_prompt = (
        "次のニュースを解析してください。\n\n"
        f"タイトル: {news.title}\n"
        f"URL: {news.link}\n"
        f"公開: {news.published}\n"
        f"本文/要約:\n{news.summary[:3000]}"
    )

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        raw_text = _extract_response_text(response)
        data = _extract_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini 解析に失敗したためフォールバックを使用: %s", exc)
        return _fallback_result(news)

    has_links_raw = data.get("has_product_links", None)
    if isinstance(has_links_raw, bool):
        has_links = has_links_raw
    elif isinstance(has_links_raw, str):
        has_links = has_links_raw.strip().lower() in ("true", "1", "yes")
    else:
        has_links = _infer_has_product_links(news)

    result: dict[str, Any] = {
        "has_product_links": has_links,
        "keyword": str(data.get("keyword", "")).strip(),
        "article_title": str(data.get("article_title", "")).strip(),
        "article_body": str(data.get("article_body", "")).strip(),
        "tweet_text": str(data.get("tweet_text", "")).strip(),
    }
    if not result["keyword"] or not result["article_title"] or not result["article_body"] or not result["tweet_text"]:
        logger.warning("Gemini 応答に欠落キーがあるためフォールバックを使用")
        return _fallback_result(news)

    result["article_body"] = re.sub(r"<[^>]+>", "", result["article_body"]).strip()
    result["tweet_text"] = result["tweet_text"][:140]
    return result
