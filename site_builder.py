# ==========================================
# Version: 1.0.0
# Date: 2026-09-16
# Summary: メディア型トップと個別記事HTMLの生成
# ==========================================
"""GitHub Pages 向けメディア型ページ生成。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DOCS = Path("docs")
TEMPLATES = Path("templates")
ENTRIES_FILE = DOCS / ".index_entries.json"
SITE_BASE = "https://dedicatebabe.github.io/trend-auto-system"
TOP_LIST_LIMIT = 6


@dataclass
class ArticleEntry:
    """一覧・個別記事用エントリ。"""

    article_id: str
    filename: str
    title: str
    excerpt: str
    source_link: str
    created_at: str
    has_product_links: bool = False
    keyword: str = ""
    badge: str = "NEWS"
    links: dict[str, str] = field(default_factory=dict)


def article_id_from_link(link: str) -> str:
    digest = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
    return f"n_{digest}"


def article_filename(article_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", article_id)
    return f"article_{safe}.html"


def article_public_url(article_id: str, *, base: str = SITE_BASE) -> str:
    return f"{base.rstrip('/')}/{article_filename(article_id)}"


def _load_template(name: str) -> str:
    path = TEMPLATES / name
    if not path.exists():
        raise FileNotFoundError(f"テンプレートがありません: {path}")
    return path.read_text(encoding="utf-8")


def _apply(template: str, mapping: dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def load_entries() -> list[ArticleEntry]:
    if not ENTRIES_FILE.exists():
        return []
    try:
        data = json.loads(ENTRIES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("entries 読み込み失敗: %s", exc)
        return []
    rows = data.get("entries", [])
    entries: list[ArticleEntry] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("article_id", "")).strip()
        filename = str(row.get("filename", "")).strip()
        if not aid or not filename:
            continue
        entries.append(
            ArticleEntry(
                article_id=aid,
                filename=filename,
                title=str(row.get("title", "") or aid),
                excerpt=str(row.get("excerpt", "") or ""),
                source_link=str(row.get("source_link", "") or ""),
                created_at=str(row.get("created_at", "") or ""),
                has_product_links=bool(row.get("has_product_links", False)),
                keyword=str(row.get("keyword", "") or ""),
                badge=str(row.get("badge", "NEWS") or "NEWS"),
                links={str(k): str(v) for k, v in (row.get("links") or {}).items()},
            )
        )
    return entries


def save_entries(entries: list[ArticleEntry]) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": [
            {
                "article_id": e.article_id,
                "filename": e.filename,
                "title": e.title,
                "excerpt": e.excerpt,
                "source_link": e.source_link,
                "created_at": e.created_at,
                "has_product_links": e.has_product_links,
                "keyword": e.keyword,
                "badge": e.badge,
                "links": e.links,
            }
            for e in entries
        ]
    }
    ENTRIES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _plain_to_paragraphs(text: str) -> str:
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text.strip()) if c.strip()]
    if not chunks:
        chunks = [text.strip()] if text.strip() else ["記事本文はありません。"]
    return "\n".join(f"<p>{html.escape(c)}</p>" for c in chunks)


def _product_links_html(links: dict[str, str], *, has_product_links: bool) -> str:
    if not has_product_links:
        return (
            '<div class="link-panel news-only">'
            "<p>この記事は読み物・ニュース紹介です。商品リンクは掲載していません。</p>"
            "</div>"
        )
    items = [
        ("amazon", "Amazonで見る", "btn-amazon"),
        ("rakuten", "楽天で見る", "btn-rakuten"),
        ("mercari", "メルカリで相場を見る", "btn-mercari"),
        ("surugaya", "駿河屋で探す", "btn-surugaya"),
    ]
    buttons: list[str] = []
    for key, label, cls in items:
        url = links.get(key, "").strip()
        if not url:
            continue
        buttons.append(
            f'<a class="btn {cls}" href="{html.escape(url, quote=True)}" '
            f'rel="nofollow sponsored noopener" target="_blank">{html.escape(label)}</a>'
        )
    if not buttons:
        return ""
    return (
        '<div class="link-panel">'
        "<p class=\"link-label\">在庫・相場を比較する</p>"
        f'<div class="btn-grid">{"".join(buttons)}</div>'
        "</div>"
    )


def render_article_page(
    *,
    title: str,
    body: str,
    source_link: str,
    created_at: str,
    has_product_links: bool,
    links: dict[str, str],
    canonical_url: str,
) -> str:
    excerpt = re.sub(r"\s+", " ", body).strip()[:120]
    badge = "商品ピックアップ" if has_product_links else "ニュース"
    template = _load_template("article.html")
    return _apply(
        template,
        {
            "PAGE_TITLE": html.escape(title),
            "META_DESCRIPTION": html.escape(excerpt),
            "CANONICAL_URL": html.escape(canonical_url),
            "BADGE": html.escape(badge),
            "PUBLISH_DATE": html.escape(created_at[:10]),
            "ARTICLE_BODY": _plain_to_paragraphs(body),
            "SOURCE_URL": html.escape(source_link, quote=True),
            "PRODUCT_LINKS": _product_links_html(
                links, has_product_links=has_product_links
            ),
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def _render_list_item(entry: ArticleEntry) -> str:
    return (
        f'<a class="story-item" href="{html.escape(entry.filename)}">'
        f'<span class="story-badge">{html.escape(entry.badge)}</span>'
        f"<h3>{html.escape(entry.title)}</h3>"
        f'<p>{html.escape(entry.excerpt[:110])}</p>'
        f'<span class="story-date">{html.escape(entry.created_at[:10])}</span>'
        f"</a>"
    )


def render_index_page(entries: list[ArticleEntry]) -> str:
    sorted_entries = sorted(entries, key=lambda e: e.created_at, reverse=True)
    template = _load_template("index.html")
    if not sorted_entries:
        featured = (
            '<div class="featured empty">'
            "<h2>まだ記事がありません</h2>"
            "<p>自動更新後に最新トピックが表示されます。</p>"
            "</div>"
        )
        latest = ""
    else:
        top = sorted_entries[0]
        featured = (
            f'<a class="featured" href="{html.escape(top.filename)}">'
            f'<span class="eyebrow">{html.escape(top.badge)}</span>'
            f"<h2>{html.escape(top.title)}</h2>"
            f"<p>{html.escape(top.excerpt[:160])}</p>"
            f'<span class="read-more">記事を読む →</span>'
            f"</a>"
        )
        rest = sorted_entries[1:TOP_LIST_LIMIT]
        latest = "\n".join(_render_list_item(e) for e in rest)
    return _apply(
        template,
        {
            "FEATURED_BLOCK": featured,
            "LATEST_LIST": latest or '<p class="muted">追加の記事はまだありません。</p>',
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def publish_article(
    *,
    source_link: str,
    title: str,
    body: str,
    has_product_links: bool,
    keyword: str,
    links: dict[str, str] | None = None,
    created_at: str | None = None,
) -> ArticleEntry:
    """個別記事を書き、entries と index を更新する。"""
    DOCS.mkdir(parents=True, exist_ok=True)
    aid = article_id_from_link(source_link)
    filename = article_filename(aid)
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    excerpt = re.sub(r"\s+", " ", body).strip()[:140]
    badge = "商品" if has_product_links else "NEWS"
    entry = ArticleEntry(
        article_id=aid,
        filename=filename,
        title=title,
        excerpt=excerpt,
        source_link=source_link,
        created_at=stamp,
        has_product_links=has_product_links,
        keyword=keyword,
        badge=badge,
        links=links or {},
    )

    page = render_article_page(
        title=title,
        body=body,
        source_link=source_link,
        created_at=stamp,
        has_product_links=has_product_links,
        links=entry.links,
        canonical_url=article_public_url(aid),
    )
    (DOCS / filename).write_text(page, encoding="utf-8")
    logger.info("個別記事を出力: %s", filename)

    entries = [e for e in load_entries() if e.article_id != aid]
    entries.append(entry)
    entries = sorted(entries, key=lambda e: e.created_at, reverse=True)[:80]
    save_entries(entries)

    index_html = render_index_page(entries)
    (DOCS / "index.html").write_text(index_html, encoding="utf-8")
    logger.info("index.html を再生成（件数=%s）", len(entries))
    return entry
