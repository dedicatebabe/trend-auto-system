# ==========================================
# Version: 1.0.0
# Date: 2026-09-13
# Summary: Trend Pick の記事・index 生成
# ==========================================
"""GitHub Pages 向け HTML 生成モジュール。"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from modules.ai_generator import extract_card_summary
from modules.topic_source import TrendItem

logger = logging.getLogger(__name__)

DOCS_DIR_NAME = "docs"
TEMPLATES_DIR_NAME = "templates"
INDEX_ENTRIES_FILE = ".index_entries.json"
SITE_NAME = "Trend Pick"

CATEGORY_MEDIA = {
    "anime": "media-anime",
    "gadget": "media-gadget",
    "goods": "media-goods",
    "game": "media-gadget",
}

CATEGORY_SPAN = {
    "anime": "ANIME",
    "gadget": "GADGET",
    "goods": "GOODS",
    "game": "GAME",
}


@dataclass
class IndexEntry:
    """トップページ一覧用エントリ。"""

    topic_id: str
    title: str
    article_filename: str
    created_at: str
    category: str = "goods"
    category_label: str = "トレンド"
    summary: str = ""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def docs_dir() -> Path:
    path = _project_root() / DOCS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def templates_dir() -> Path:
    return _project_root() / TEMPLATES_DIR_NAME


def article_filename_for(topic_id: str) -> str:
    safe_id = re.sub(r"[^\w\-]", "_", topic_id)
    return f"article_{safe_id}.html"


def build_cushion_page_url(base_url: str, topic_id: str) -> str:
    """個別記事の公開 URL。"""
    base = base_url.rstrip("/")
    return f"{base}/{article_filename_for(topic_id)}"


def _load_template(name: str) -> str:
    path = templates_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"テンプレートが見つかりません: {path}")
    return path.read_text(encoding="utf-8")


def _apply_template(template: str, mapping: dict[str, str]) -> str:
    rendered = template
    for key, value in mapping.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _load_index_entries(docs_path: Path) -> list[IndexEntry]:
    meta_path = docs_path / INDEX_ENTRIES_FILE
    if not meta_path.exists():
        return []
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("index メタデータ読み込み失敗: %s", exc)
        return []
    entries: list[IndexEntry] = []
    for row in data.get("entries", []):
        if not isinstance(row, dict):
            continue
        topic_id = str(row.get("topic_id", "")).strip()
        filename = str(row.get("article_filename", "")).strip()
        if not topic_id or not filename:
            continue
        entries.append(
            IndexEntry(
                topic_id=topic_id,
                title=str(row.get("title", "") or topic_id),
                article_filename=filename,
                created_at=str(row.get("created_at", "") or ""),
                category=str(row.get("category", "goods") or "goods"),
                category_label=str(row.get("category_label", "トレンド") or "トレンド"),
                summary=str(row.get("summary", "") or ""),
            )
        )
    return entries


def _save_index_entries(docs_path: Path, entries: list[IndexEntry]) -> None:
    meta_path = docs_path / INDEX_ENTRIES_FILE
    payload = {
        "entries": [
            {
                "topic_id": e.topic_id,
                "title": e.title,
                "article_filename": e.article_filename,
                "created_at": e.created_at,
                "category": e.category,
                "category_label": e.category_label,
                "summary": e.summary,
            }
            for e in entries
        ]
    }
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _render_article_page(
    item: TrendItem,
    article_html_body: str,
    *,
    pages_base_url: str,
    summary: str = "",
    created_at: str | None = None,
) -> str:
    page_title = html.escape(item.title)
    meta_source = (summary or item.description.replace("\n", " ")).strip()
    description_meta = html.escape(meta_source[:160])
    canonical = html.escape(build_cushion_page_url(pages_base_url, item.topic_id))
    mercari = html.escape(item.mercari_url or "#", quote=True)
    surugaya = html.escape(item.surugaya_url or "#", quote=True)
    category = html.escape(item.category_label)
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    publish_date = html.escape(stamp[:10])

    og_image_tag = ""
    hero_block = ""
    if item.image_url:
        image = html.escape(item.image_url)
        og_image_tag = f'<meta property="og:image" content="{image}">'
        hero_block = (
            f'<div class="hero-media"><img src="{image}" alt="{page_title}" '
            f'loading="lazy"></div>'
        )

    template = _load_template("article.html")
    return _apply_template(
        template,
        {
            "PAGE_TITLE": page_title,
            "META_DESCRIPTION": description_meta,
            "CANONICAL_URL": canonical,
            "OG_IMAGE_TAG": og_image_tag,
            "HERO_BLOCK": hero_block,
            "CATEGORY": category,
            "PUBLISH_DATE": publish_date,
            "ARTICLE_BODY": article_html_body,
            "MERCARI_URL": mercari,
            "SURUGAYA_URL": surugaya,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def _render_card(entry: IndexEntry) -> str:
    title = html.escape(entry.title)
    href = html.escape(entry.article_filename)
    summary = html.escape(entry.summary or "レビューを読む")
    badge = html.escape(entry.category_label)
    media = CATEGORY_MEDIA.get(entry.category, "media-goods")
    span = CATEGORY_SPAN.get(entry.category, "TREND")
    return (
        f'<a class="card card-link" href="{href}" id="{html.escape(entry.category)}">'
        f'<div class="card-media {media}"><span>{span}</span></div>'
        f'<div class="card-body">'
        f'<span class="badge">{badge}</span>'
        f"<h3>{title}</h3>"
        f"<p>{summary}</p>"
        f"</div></a>"
    )


def _render_index_page(entries: list[IndexEntry], *, pages_base_url: str) -> str:
    sorted_entries = sorted(entries, key=lambda e: e.created_at, reverse=True)
    if sorted_entries:
        cards = "\n".join(_render_card(e) for e in sorted_entries)
    else:
        cards = '<p class="empty">まだ記事がありません。自動投稿後に表示されます。</p>'

    template = _load_template("index.html")
    return _apply_template(
        template,
        {
            "PAGE_TITLE": f"{SITE_NAME} - 最新話題・アニメ・ゲーム・グッズレビュー",
            "META_DESCRIPTION": (
                "アニメ、ゲーム、最新ガジェット、トレンドグッズをわかりやすく紹介するレビューメディア。"
            ),
            "CANONICAL_URL": html.escape(pages_base_url.rstrip("/") + "/"),
            "CARD_GRID": cards,
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def write_article_and_update_index(
    item: TrendItem,
    article_html_body: str,
    *,
    github_pages_base_url: str,
    summary: str | None = None,
    created_at: str | None = None,
) -> Path:
    """個別記事を書き出し、index.html とメタデータを更新する。"""
    docs_path = docs_dir()
    filename = article_filename_for(item.topic_id)
    article_path = docs_path / filename
    now_iso = created_at or datetime.now(timezone.utc).isoformat()
    card_summary = (summary or "").strip() or extract_card_summary(
        article_html_body,
        item,
    )

    page_html = _render_article_page(
        item,
        article_html_body,
        pages_base_url=github_pages_base_url,
        summary=card_summary,
        created_at=now_iso,
    )
    article_path.write_text(page_html, encoding="utf-8")
    logger.info("記事 HTML を出力: %s", article_path)

    entries = _load_index_entries(docs_path)
    entries = [e for e in entries if e.topic_id != item.topic_id]
    entries.append(
        IndexEntry(
            topic_id=item.topic_id,
            title=item.title,
            article_filename=filename,
            created_at=now_iso,
            category=item.category,
            category_label=item.category_label,
            summary=card_summary,
        )
    )
    _save_index_entries(docs_path, entries)

    index_html = _render_index_page(entries, pages_base_url=github_pages_base_url)
    (docs_path / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("index.html を更新しました（件数=%s）", len(entries))
    return article_path
