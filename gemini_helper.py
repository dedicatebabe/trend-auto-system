# ==========================================
# Version: 3.2.0
# Date: 2026-09-16
# Summary: 商品紹介をフック＋見出し構成の惹きつける文面に変更
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
- article_title: Web記事用のキャッチーな日本語タイトル。買う／購入／相場などの販売色は出さない
- article_body: 見どころ文章。プレーンテキストのみ（HTML禁止）。200〜400字。
  話題の背景とポイントを中心に書く。購入誘導やメタ説明は禁止。
- tweet_text: X投稿。フック＋詳細誘導。100文字前後

出力は JSON のみ。
"""

PRODUCT_SYSTEM_PROMPT = """あなたはアフィリエイト媒体の編集者兼コピーライターです。
読者が「ちょっと見たい」と思う温度感で商品を紹介してください。
カタログ説明・事務的な紹介文・テンプレ感のある文章は禁止。
アダルト・性的・過激な内容は禁止。一般向けのみ。
嘘のスペック、架空の口コミ、過度な煽り（今すぐ買え等）は禁止。
値段や在庫は変動しうる前提で書く。

禁止:
- 「管理番号」「商品番号」「品番」だけをタイトルや本文の主役にすること
- 「フィギュア（楽天） 12345678」のようなスラッグ／IDだけの商品名をそのまま書くこと
- 「ご紹介です」「向いています」「チェックしてください」だけの薄い文
- 商品名が不明なのに推測で固有名を捏造すること

必須キー:
- article_title: 日本語タイトル。実商品名＋感情フック。32〜42字前後
- article_body: プレーンテキストのみ。次の形式を厳守（見出し行は ## で始める）:

1段落目（見出しなし）: フック。なぜ今これが気になるかを2〜4文で。事務説明禁止。

## 刺さる人
誰のどんな欲に刺さるか。1〜3文。

## 見るべきところ
商品ページで確認したい見どころを、箇条書き3点（各行は「- 」で始める）。

## ひとこと注意
在庫・価格・仕様の変動など、短く1〜2文。

全体で280〜420字。テンプレ感を消し、編集部が推す口調にする。
- tweet_text: X投稿。100文字前後。フック強め＋詳細誘導
- keyword: 短い検索語（作品名・型番・一般名）。数字だけの管理番号は禁止

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


def _fallback_product_result(product: Any) -> dict[str, Any]:
    title = str(getattr(product, "title", "") or "注目アイテム")
    price = getattr(product, "price", None)
    source = str(getattr(product, "source", "") or "")
    shop = {"amazon": "Amazon", "rakuten": "楽天", "mercari": "メルカリ"}.get(source, "ショップ")
    price_txt = f"{price:,}円前後" if isinstance(price, int) and price > 0 else "価格は商品ページで要確認"
    short = title[:40]
    body = (
        f"「{title}」がいま静かに、でも確実に目立っている。{shop}まわりで反応が集まっているので、気になる人は一度見た方が早い。\n\n"
        f"## 刺さる人\n"
        f"作品ファン、コレクションを増やしたい人、手触りや造形を重視する人。\n\n"
        f"## 見るべきところ\n"
        f"- 見た目・付属・サイズ感が自分のイメージと合うか\n"
        f"- 参考価格の目安（{price_txt}）\n"
        f"- 在庫と発送条件の最新情報\n\n"
        f"## ひとこと注意\n"
        f"価格と在庫は変わりやすい。判断は商品ページの最新表示で。"
    )
    return {
        "article_title": f"{short}｜いま押さえておきたい1品",
        "article_body": body,
        "tweet_text": f"気になる人、見て。{short}",
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
        "次の商品を、読者がつい見たくなる記事にしてください。\n"
        "事務的な紹介ではなく、編集部が推す温度感で。\n\n"
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
                temperature=0.75,
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
                temperature=0.5,
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
    # 話題パイプラインでは常に false に固定
    result["has_product_links"] = False
    return result
