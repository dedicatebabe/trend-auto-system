# ==========================================
# Version: 1.0.1
# Date: 2026-09-13
# Summary: google-genai を遅延 import（offline 実行対応）
# ==========================================
"""Google Gemini API を用いた一般向けコンテンツ生成。"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path
from typing import Any

from modules.topic_source import TrendItem

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.6-flash"
MAX_TWEET_LENGTH = 280
CTA_LINE = "👇詳しいレビューはこちら"


def create_gemini_client(api_key: str | None = None) -> Any:
    """Gemini クライアントを生成する。"""
    from google import genai

    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise ValueError("GEMINI_API_KEY が設定されていません。")
    return genai.Client(api_key=key)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_prompt_file(name: str) -> str:
    """prompts/ 配下のプロンプトを読み込む（ヘッダーコメント行は除去）。"""
    path = _project_root() / "prompts" / name
    if not path.exists():
        raise FileNotFoundError(f"プロンプトファイルがありません: {path}")
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# ===") or stripped.startswith("# Version:"):
            continue
        if stripped.startswith("# Date:") or stripped.startswith("# Summary:"):
            continue
        if stripped == "#" or stripped.startswith("# ====="):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _build_item_context(item: TrendItem) -> str:
    """プロンプト用トピック情報。"""
    return "\n".join(
        [
            f"タイトル: {item.title}",
            f"topic_id: {item.topic_id}",
            f"カテゴリ: {item.category_label}",
            f"検索キーワード: {item.search_keyword}",
            f"要約ヒント: {item.summary_hint}",
            f"概要:\n{item.description}",
        ]
    )


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:html)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_response_text(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return ""
    candidate = candidates[0]
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    texts: list[str] = []
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


def _generate_text(
    client: Any,
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
) -> str:
    from google.genai import types

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                max_output_tokens=4096,
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                ],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini API 呼び出し失敗: %s", exc)
        return ""
    return _extract_response_text(response)


def _fallback_article_html(item: TrendItem) -> str:
    """Gemini 失敗時の一般向けレビュー HTML。"""
    title = html.escape(item.title)
    hint = html.escape(item.summary_hint or "購入前の確認ポイント")
    keyword = html.escape(item.search_keyword)
    return f"""<h2>この記事のポイント</h2>
<p>{title}について、買う前・探す前に押さえておきたい観点を整理しました。焦点は「{hint}」です。</p>
<h2>確認しておきたいこと</h2>
<ul>
  <li>公式情報とスペック（対応端末・収録内容・サイズなど）を先に確認する</li>
  <li>新品／中古の状態差と、返品条件の有無を見比べる</li>
  <li>検索キーワード「{keyword}」で候補を絞り、価格帯の相場感をつかむ</li>
</ul>
<h2>向いている人</h2>
<p>情報を短時間で整理してから、メルカリや駿河屋で実物・在庫を確認したい人向けです。</p>
<h2>注意点</h2>
<p>在庫・価格・仕様は変動します。最終判断は各ショップの商品ページで行ってください。アダルト商品や年齢制限付きコンテンツは扱いません。</p>
<h2>まとめ</h2>
<p>まずは条件を決めて検索し、状態と価格が合うものを選ぶのが失敗しにくい進め方です。</p>
"""


def _normalize_x_post(text: str, *, article_url: str) -> str:
    cleaned = re.sub(r"\s+\n", "\n", text).strip()
    if article_url and article_url not in cleaned:
        cleaned = f"{cleaned}\n{article_url}"
    if len(cleaned) <= MAX_TWEET_LENGTH:
        return cleaned
    reserve = len(article_url) + 2
    body = cleaned.replace(article_url, "").strip()
    body = body[: max(0, MAX_TWEET_LENGTH - reserve - 1)].rstrip()
    return f"{body}\n{article_url}"


def _fallback_x_post_text(item: TrendItem, *, article_url: str) -> str:
    short = item.title[:32]
    text = (
        "これ、買う前に一度確認しておくと安心。\n"
        f"「{short}」のチェックポイントを短くまとめました。\n"
        f"{CTA_LINE}\n"
        f"{article_url}\n"
        "#トレンドレビュー #ガジェット"
    )
    return _normalize_x_post(text, article_url=article_url)


def _plain_text_from_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html or "")
    return re.sub(r"\s+", " ", text).strip()


def extract_card_summary(article_html_body: str, item: TrendItem) -> str:
    """カード用の短い要約。"""
    if item.summary_hint:
        return item.summary_hint[:80]
    blob = _plain_text_from_html(article_html_body)
    if blob:
        return blob[:80]
    return "購入前のポイントを短く整理したレビュー"


def generate_article_html(client: Any, item: TrendItem) -> str:
    """記事本文 HTML 断片を生成する。"""
    system_prompt = _load_prompt_file("article.txt")
    user_prompt = (
        "次のトピックについて、指定フォーマットの HTML 断片だけを出力してください。\n\n"
        + _build_item_context(item)
    )
    raw = _generate_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.7,
    )
    cleaned = _strip_code_fence(raw)
    if "<h2" not in cleaned.lower():
        logger.warning("記事生成に失敗したためフォールバックを使用します。")
        return _fallback_article_html(item)
    return cleaned


def generate_x_post_text(
    client: Any,
    item: TrendItem,
    *,
    cushion_page_url: str,
) -> str:
    """X 投稿文を生成する。"""
    system_prompt = _load_prompt_file("x_post.txt")
    user_prompt = (
        "次のトピックの X 投稿を1つだけ出力してください。\n"
        f"個別記事URL: {cushion_page_url}\n\n"
        + _build_item_context(item)
    )
    raw = _generate_text(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.85,
    )
    cleaned = raw.strip()
    if not cleaned:
        return _fallback_x_post_text(item, article_url=cushion_page_url)
    return _normalize_x_post(cleaned, article_url=cushion_page_url)
