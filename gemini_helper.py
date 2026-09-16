# ==========================================
# Version: 3.3.0
# Date: 2026-09-17
# Summary: タイトル・本文を煽りAI口調から記事調に変更
# ==========================================
"""Gemini API によるニュース／商品解析ヘルパー。"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from rss_fetcher import NewsItem

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.6-flash"

SYSTEM_PROMPT = """あなたは日本のアニメ・ゲーム・ホビー・ガジェットのトレンド編集者です。
入力されたプレスリリース／ニュースを読み、次の JSON オブジェクトだけを出力してください。
アダルト・性的・過激な内容は禁止。一般向け（全年齢）のみ。
体裁は「いまの話題紹介」が主。購入誘導・ショップ検索誘導はしない。

必須キー:
- has_product_links: 常に false（このパイプラインでは商品ページ直リンクを扱わない）
- keyword: 話題の短い語（作品名・イベント名・固有名詞）
- article_title: Web記事用の落ち着いた日本語タイトル。買う／購入／相場などの販売色は出さない
- article_body: 見どころ文章。プレーンテキストのみ（HTML禁止）。200〜400字。
  話題の背景とポイントを中心に書く。購入誘導やメタ説明は禁止。
- tweet_text: X投稿。事実ベースの誘導。100文字前後

出力は JSON のみ。
"""

PRODUCT_SYSTEM_PROMPT = """あなたはアニメ・ゲーム・ホビー系のウェブメディア編集者です。
商品紹介を書きますが、広告コピーやSNS煽りではなく「普通の記事」として書いてください。
アダルト・性的・過激な内容は禁止。一般向けのみ。
嘘のスペック、架空の口コミは禁止。値段や在庫は変動しうる前提で書く。

タイトルのルール（重要）:
- 日本のウェブ記事らしい落ち着いた見出し
- 商品名や作品名を自然に入れる
- 禁止: すぎる / やばい / 最高 / 胸熱 / 一目惚れ / 止まらない / グッとくる / 待望の / 絶対に / 激アツ / ！の連打
- 「感情フック＋商品名！」のAI定型は禁止
- 良い例: 「『葬送のフリーレン』フリーレンの1/7フィギュア、再販分をチェック」
- 良い例: 「ベイブレードX BX-56 ストリングランチャーLの仕様を整理」
- 良い例: 「ONE PIECEカードゲーム『神の支配』BOX、気になる点まとめ」
- 28〜40字前後

本文のルール:
- 読み物として自然な日本語。宣伝口調・テンプレ感は禁止
- 「ご紹介です」「向いています」「チェックしてください」だけの薄い文は禁止
- 「管理番号」「商品番号」やスラッグ名だけの紹介は禁止
- 次の形式のプレーンテキスト（見出しは ## ）:

1段落目（見出しなし）: 導入。何の商品か、いま話題になっている理由を2〜4文で淡々と。

## 見どころ
箇条書き3点（各行は「- 」で始める）。事実ベースで短く。

## 向いている人
誰向けかを1〜2文。押し売りしない。

## 購入前の確認
在庫・価格・仕様の変動など、短く1〜2文。

全体で260〜400字。編集者が普通に書く記事の温度感。

必須キー:
- article_title
- article_body
- tweet_text: X投稿。80〜110字。煽らず、記事への誘導がある一文
- keyword: 短い検索語（作品名・型番・一般名）

出力は JSON のみ。
"""

_AI_TITLE_NG = (
    "すぎる",
    "やばい",
    "最高",
    "胸熱",
    "一目惚れ",
    "止まらない",
    "グッとくる",
    "待望の",
    "絶対に",
    "神すぎ",
    "激アツ",
    "悶絶",
    "押しすぎ",
)


def _title_looks_ai(title: str) -> bool:
    """煽り・AI定型タイトルか。"""
    text = (title or "").strip()
    if not text:
        return True
    if text.count("！") + text.count("!") >= 2:
        return True
    if any(ng in text for ng in _AI_TITLE_NG):
        return True
    # 「感情！商品名」の定型（前半で感嘆符）
    if "！" in text:
        idx = text.index("！")
        if idx <= 16 and len(text) > 20:
            return True
    return False


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
    """PR TIMES話題記事では検索アフィを付けない。"""
    _ = news
    return False


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
        f"「{short_title}」のポイントをまとめました。詳細はこちら。"
    )
    return {
        "has_product_links": has_links,
        "keyword": keyword,
        "article_title": short_title,
        "article_body": body,
        "tweet_text": tweet[:120],
    }


def _fallback_product_result(product: Any) -> dict[str, Any]:
    title = str(getattr(product, "title", "") or "注目アイテム")
    price = getattr(product, "price", None)
    source = str(getattr(product, "source", "") or "")
    shop = {"amazon": "Amazon", "rakuten": "楽天", "mercari": "メルカリ"}.get(source, "ショップ")
    price_txt = f"{price:,}円前後" if isinstance(price, int) and price > 0 else "価格は商品ページで要確認"
    short = title[:36]
    body = (
        f"「{title}」が{shop}で扱われている。気になる人向けに、見どころだけ短く整理する。\n\n"
        f"## 見どころ\n"
        f"- 見た目・付属・サイズ感が自分のイメージと合うか\n"
        f"- 参考価格の目安（{price_txt}）\n"
        f"- 在庫と発送条件の最新情報\n\n"
        f"## 向いている人\n"
        f"作品ファンや、同系統のコレクションを増やしたい人。\n\n"
        f"## 購入前の確認\n"
        f"価格と在庫は変わりやすい。判断は商品ページの最新表示で。"
    )
    return {
        "article_title": f"{short}の見どころを整理",
        "article_body": body,
        "tweet_text": f"{short}のポイントをまとめました。",
        "keyword": short[:20],
    }


def analyze_product_with_gemini(
    product: Any,
    *,
    api_key: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """
    商品情報から紹介文を生成する。

    キー: article_title, article_body, tweet_text, keyword
    """
    from google.genai import types

    client = _create_client(api_key)
    model_id = (model_name or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)).strip()
    price = getattr(product, "price", None)
    user_prompt = (
        "次の商品を、普通のウェブ記事として書いてください。\n"
        "煽り・宣伝コピー・AIっぽい感嘆表現は使わないでください。\n\n"
        f"商品名: {getattr(product, 'title', '')}\n"
        f"ショップ: {getattr(product, 'source', '')}\n"
        f"価格: {price if price is not None else '不明'}\n"
        f"評価: {getattr(product, 'rating', None)}\n"
        f"レビュー数: {getattr(product, 'review_count', None)}\n"
        f"ランク: {getattr(product, 'rank', None)}\n"
        f"商品URL: {getattr(product, 'url', '')}\n"
    )
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=PRODUCT_SYSTEM_PROMPT,
                temperature=0.45,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        raw_text = _extract_response_text(response)
        data = _extract_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("商品Gemini生成失敗、フォールバック: %s", exc)
        return _fallback_product_result(product)

    result = {
        "article_title": str(data.get("article_title", "")).strip(),
        "article_body": re.sub(r"<[^>]+>", "", str(data.get("article_body", ""))).strip(),
        "tweet_text": str(data.get("tweet_text", "")).strip()[:140],
        "keyword": str(data.get("keyword", "")).strip(),
    }
    if not result["article_title"] or not result["article_body"] or not result["tweet_text"]:
        return _fallback_product_result(product)
    if _title_looks_ai(result["article_title"]):
        logger.info("AIっぽいタイトルのためフォールバック: %s", result["article_title"][:40])
        fb = _fallback_product_result(product)
        # 本文はGeminiのものを活かし、タイトルだけ記事調に寄せる
        result["article_title"] = fb["article_title"]
        if _title_looks_ai(result["tweet_text"]):
            result["tweet_text"] = fb["tweet_text"]
    if not result["keyword"]:
        result["keyword"] = str(getattr(product, "title", ""))[:20]
    return result


def analyze_news_with_gemini(
    news: NewsItem,
    *,
    api_key: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """
    ニュース1件を解析する。

    キー: has_product_links, keyword, article_title, article_body, tweet_text
    """
    from google.genai import types

    client = _create_client(api_key)
    model_id = (model_name or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)).strip()
    user_prompt = (
        "次のニュースを解析してください。\n\n"
        f"タイトル: {news.title}\n"
        f"要約: {news.summary}\n"
        f"URL: {news.link}\n"
        f"公開日: {news.published}\n"
    )
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.4,
                max_output_tokens=2048,
                response_mime_type="application/json",
            ),
        )
        raw_text = _extract_response_text(response)
        data = _extract_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini生成失敗、フォールバック: %s", exc)
        return _fallback_result(news)

    result = {
        "has_product_links": bool(data.get("has_product_links", False)),
        "keyword": str(data.get("keyword", "")).strip(),
        "article_title": str(data.get("article_title", "")).strip(),
        "article_body": re.sub(r"<[^>]+>", "", str(data.get("article_body", ""))).strip(),
        "tweet_text": str(data.get("tweet_text", "")).strip()[:140],
    }
    if not result["keyword"] or not result["article_title"] or not result["article_body"] or not result["tweet_text"]:
        return _fallback_result(news)
    result["has_product_links"] = False
    return result
