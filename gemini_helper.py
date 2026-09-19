# ==========================================
# Version: 4.0.0
# Date: 2026-09-20
# Summary: たいち人格の掘り出しメモ調に本文・X投稿を刷新
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

SYSTEM_PROMPT = """あなたは日本のアニメ・ゲーム・ホビーのトレンドを追うメモ書きです。
入力されたプレスリリース／ニュースを読み、次の JSON オブジェクトだけを出力してください。
アダルト・性的・過激な内容は禁止。一般向け（全年齢）のみ。
体裁は「話題を拾ったメモ」。購入誘導・ショップ検索誘導はしない。

必須キー:
- has_product_links: 常に false
- keyword: 話題の短い語（作品名・イベント名・固有名詞）
- article_title: Web記事用の落ち着いた日本語タイトル。買う／購入／相場などの販売色は出さない
- article_body: 見どころ文章。プレーンテキストのみ（HTML禁止・##見出し禁止）。280〜450字。
- tweet_text: X本投稿。URLなし。80〜120字。事実ベース

出力は JSON のみ。
"""

PRODUCT_SYSTEM_PROMPT = """あなたは X アカウント「たいち＠アニメ・ホビー掘り出し物メモ」の編集者です。
アニメ化やトレンドニュースを見たら、すぐ原作コミックやグッズを探しにいく人のメモとして書いてください。
読者は「全巻一気読みしたい人」「フィギュアやグッズを探している人」。
アダルト・性的・過激な内容は禁止。一般向けのみ。
嘘のスペック、架空の口コミは禁止。値段や在庫は変動しうる前提で書く。

人格・トーン:
- 自分用の掘り出しメモを公開している感じ
- 「気になったので探してみた」「参考になれば」くらいの距離感
- カタログ説明・通販レビュー・マーケコピーにしない
- 翻訳調・AIっぽい感嘆・煽りは禁止

タイトルのルール:
- 作品名・商品名を自然に入れる落ち着いた日本語
- 禁止: すぎる / やばい / 最高 / 胸熱 / 一目惚れ / 止まらない / グッとくる / 待望の / 絶対に / 激アツ / 刺さる / ！の連打
- 「〜を整理」「〜まとめ」「〜の特徴」「向き不向き」の連発も避ける
- 良い例: 「アニメ化が気になった人向け。原作と関連グッズの探しメモ」
- 良い例: 「『葬送のフリーレン』1/7フィギュア、再販を拾ってみた」
- 良い例: 「ONE PIECEカード『神の支配』BOX、予約状況のメモ」
- 28〜42字前後

本文のルール:
- プレーンテキストのみ（HTML禁止）
- ## 見出しは禁止（ポイント／向き不向き／注意点／見どころなども使わない）
- 箇条書き（「- 」）も原則禁止。2〜4段落の読み物にする
- 1段落: 何を拾ったか・なぜメモしたか（トレンド／予約／掘り出しなど）
- 2段落: どんな人の参考になるか、探すときの観点（原作一気読み／フィギュア探しなど）
- 3段落: ショップで在庫・価格が違うので比較して、という短い補足
- 薄い紹介文は禁止。固有名詞を入れ、320〜520字

tweet_text のルール:
- Xの本投稿用。URLは絶対に書かない（リンクは別投稿）
- 80〜120字。掘り出しメモの口調。煽らない
- 「詳細はこちら」「リンクはプロフ」などの誘導定型は不要
- 良い例: 「アニメ化ニュース見て原作とグッズ探してた。一気読みしたい人向けにメモった」

必須キー:
- article_title
- article_body
- tweet_text
- keyword: 短い検索語

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
    "刺さる",
)

_STRIP_HEADINGS = (
    "ポイント",
    "向き不向き",
    "注意点",
    "見どころ",
    "刺さる人",
    "ひとこと注意",
    "一言注意",
    "向いている人",
    "購入前の確認",
    "見るべきところ",
    "こんな人向け",
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
    if "！" in text:
        idx = text.index("！")
        if idx <= 16 and len(text) > 20:
            return True
    return False


def _normalize_article_body(body: str) -> str:
    """見出し・箇条書きを剥がし、メモ調の段落文に近づける。"""
    raw = (body or "").strip()
    if not raw:
        return ""
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = " ".join(buffer).strip()
        if text:
            paragraphs.append(text)
        buffer = []

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            flush()
            continue
        if s.startswith("## "):
            flush()
            heading = s[3:].strip()
            if heading in _STRIP_HEADINGS:
                continue
            buffer.append(heading + "。")
            continue
        if s.startswith(("- ", "・")):
            item = s[2:].strip() if s.startswith("- ") else s[1:].strip()
            if item:
                buffer.append(item + "。")
            continue
        buffer.append(s)
    flush()
    return "\n\n".join(paragraphs).strip()


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
    summary = (news.summary or "話題のリリースです。")[:280]
    body = (
        f"{news.title}\n\n"
        f"{summary}\n\n"
        "気になった人向けに、ひとまず話題のポイントだけメモしておく。"
    )
    tweet = f"「{short_title}」が気になったのでメモ。原作や関連を追いたい人の参考に。"
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
        f"「{title}」を{shop}で見かけてメモ。トレンドや予約状況を追っていると、"
        f"こういう関連グッズも一緒に探しがちなので残しておく。\n\n"
        f"作品ファンで一気に揃えたい人や、フィギュア・グッズを探している人の参考になれば。"
        f"見た目・付属・サイズ感がイメージと合うかは各ページで確認を。参考価格の目安は{price_txt}。\n\n"
        f"在庫と価格はショップごとに違うので、Amazon・楽天・Yahoo・メルカリ・駿河屋あたりで見比べるのが無難。"
    )
    return {
        "article_title": f"{short}、探しメモ",
        "article_body": body,
        "tweet_text": f"{short}が気になったので探してメモ。グッズ探し中の人の参考に。",
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
        "次の商品を、たいち＠掘り出し物メモの口調で書いてください。\n"
        "見出しや箇条書きは使わず、段落だけのメモにしてください。\n"
        "tweet_text に URL は入れないでください。\n\n"
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
                temperature=0.55,
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
        "article_body": _normalize_article_body(
            re.sub(r"<[^>]+>", "", str(data.get("article_body", ""))).strip()
        ),
        "tweet_text": str(data.get("tweet_text", "")).strip()[:140],
        "keyword": str(data.get("keyword", "")).strip(),
    }
    if not result["article_title"] or not result["article_body"] or not result["tweet_text"]:
        return _fallback_product_result(product)
    if _title_looks_ai(result["article_title"]):
        logger.info("AIっぽいタイトルのためフォールバック: %s", result["article_title"][:40])
        fb = _fallback_product_result(product)
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
        "article_body": _normalize_article_body(
            re.sub(r"<[^>]+>", "", str(data.get("article_body", ""))).strip()
        ),
        "tweet_text": str(data.get("tweet_text", "")).strip()[:140],
    }
    if not result["keyword"] or not result["article_title"] or not result["article_body"] or not result["tweet_text"]:
        return _fallback_result(news)
    result["has_product_links"] = False
    return result
