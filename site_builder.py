# ==========================================
# Version: 1.1.1
# Date: 2026-09-16
# Summary: 読み物記事の買い物リンク無し断りを削除
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

BADGE_CLASS = {
    "NEWS": "badge-news",
    "注目アイテム": "badge-item",
    "トレンドグッズ": "badge-goods",
    "アニメ": "badge-anime",
    "ゲーム": "badge-game",
    "ガジェット": "badge-gadget",
}


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
    image_seed: int = 1
    links: dict[str, str] = field(default_factory=dict)


def article_id_from_link(link: str) -> str:
    digest = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
    return f"n_{digest}"


def article_filename(article_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", article_id)
    return f"article_{safe}.html"


def article_public_url(article_id: str, *, base: str = SITE_BASE) -> str:
    return f"{base.rstrip('/')}/{article_filename(article_id)}"


def _badge_for(*, has_product_links: bool, badge: str | None = None) -> str:
    if badge and badge.strip():
        raw = badge.strip()
        if raw in ("商品", "商品ピックアップ"):
            return "注目アイテム"
        return raw
    return "注目アイテム" if has_product_links else "NEWS"


def _image_seed_from_id(article_id: str) -> int:
    digest = hashlib.md5(article_id.encode("utf-8")).hexdigest()
    return (int(digest[:6], 16) % 90) + 1


def _thumb_url(seed: int) -> str:
    return f"https://picsum.photos/600/400?random={int(seed)}"


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
        has_links = bool(row.get("has_product_links", False))
        badge = _badge_for(
            has_product_links=has_links,
            badge=str(row.get("badge", "") or ""),
        )
        seed_raw = row.get("image_seed")
        try:
            seed = int(seed_raw) if seed_raw is not None else _image_seed_from_id(aid)
        except (TypeError, ValueError):
            seed = _image_seed_from_id(aid)
        entries.append(
            ArticleEntry(
                article_id=aid,
                filename=filename,
                title=str(row.get("title", "") or aid),
                excerpt=str(row.get("excerpt", "") or ""),
                source_link=str(row.get("source_link", "") or ""),
                created_at=str(row.get("created_at", "") or ""),
                has_product_links=has_links,
                keyword=str(row.get("keyword", "") or ""),
                badge=badge,
                image_seed=seed,
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
                "image_seed": e.image_seed,
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
        return ""
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
        '<p class="link-label">在庫・相場を比較する</p>'
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
    badge: str | None = None,
) -> str:
    excerpt = re.sub(r"\s+", " ", body).strip()[:120]
    badge_label = _badge_for(has_product_links=has_product_links, badge=badge)
    template = _load_template("article.html")
    return _apply(
        template,
        {
            "PAGE_TITLE": html.escape(title),
            "META_DESCRIPTION": html.escape(excerpt),
            "CANONICAL_URL": html.escape(canonical_url),
            "BADGE": html.escape(badge_label),
            "PUBLISH_DATE": html.escape(created_at[:10]),
            "ARTICLE_BODY": _plain_to_paragraphs(body),
            "SOURCE_URL": html.escape(source_link, quote=True),
            "PRODUCT_LINKS": _product_links_html(
                links, has_product_links=has_product_links
            ),
            "YEAR": str(datetime.now(timezone.utc).year),
        },
    )


def _badge_class(badge: str) -> str:
    return BADGE_CLASS.get(badge, "badge-news")


def _render_card(entry: ArticleEntry, *, featured: bool = False) -> str:
    cls = "card card-featured" if featured else "card"
    title_tag = "h2" if featured else "h3"
    title = html.escape(entry.title)
    excerpt = html.escape((entry.excerpt or "")[:140])
    date = html.escape(entry.created_at[:10] if entry.created_at else "")
    badge = html.escape(entry.badge)
    badge_cls = _badge_class(entry.badge)
    href = html.escape(entry.filename)
    thumb = html.escape(_thumb_url(entry.image_seed), quote=True)
    return (
        f'<a class="{cls}" href="{href}">'
        f'<div class="card-thumb"><img src="{thumb}" alt="" loading="lazy"></div>'
        f'<div class="card-body">'
        f'<span class="badge {badge_cls}">{badge}</span>'
        f"<{title_tag}>{title}</{title_tag}>"
        f'<p class="card-excerpt">{excerpt}</p>'
        f'<span class="card-date">{date}</span>'
        f"</div></a>"
    )


def render_index_page(entries: list[ArticleEntry]) -> str:
    sorted_entries = sorted(entries, key=lambda e: e.created_at, reverse=True)
    template = _load_template("index.html")
    if not sorted_entries:
        featured = (
            '<div class="card card-featured">'
            '<div class="card-body">'
            "<h2>まだ記事がありません</h2>"
            '<p class="card-excerpt">自動更新後に最新トピックが表示されます。</p>'
            "</div></div>"
        )
        latest = '<p class="empty">追加の記事はまだありません。</p>'
    else:
        top = sorted_entries[0]
        featured = _render_card(top, featured=True)
        rest = sorted_entries[1 : TOP_LIST_LIMIT + 1]
        latest = "\n".join(_render_card(e) for e in rest) or (
            '<p class="empty">追加の記事はまだありません。</p>'
        )
    return _apply(
        template,
        {
            "FEATURED_BLOCK": featured,
            "LATEST_LIST": latest,
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
    badge: str | None = None,
    image_seed: int | None = None,
) -> ArticleEntry:
    """個別記事を書き、entries と index を更新する。"""
    DOCS.mkdir(parents=True, exist_ok=True)
    aid = article_id_from_link(source_link)
    filename = article_filename(aid)
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    excerpt = re.sub(r"\s+", " ", body).strip()[:140]
    badge_label = _badge_for(has_product_links=has_product_links, badge=badge)
    seed = image_seed if image_seed is not None else _image_seed_from_id(aid)
    entry = ArticleEntry(
        article_id=aid,
        filename=filename,
        title=title,
        excerpt=excerpt,
        source_link=source_link,
        created_at=stamp,
        has_product_links=has_product_links,
        keyword=keyword,
        badge=badge_label,
        image_seed=seed,
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
        badge=badge_label,
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


def ensure_demo_volume(min_total: int = 7) -> list[ArticleEntry]:
    """
    トップの賑わい用にダミー記事を補充する。

    既存エントリが min_total 未満なら不足分を追加し、index を再生成する。
    """
    demos = [
        {
            "source_link": "https://example.com/demo/anime-spring",
            "title": "今期注目のアニメ化作品。放送前に押さえておきたい見どころ",
            "body": "話題の原作が映像化されます。\n\nキャラクター設計と世界観のどこが魅力かを、短く整理しました。",
            "badge": "アニメ",
            "keyword": "アニメ化 注目作品",
            "has_product_links": False,
            "image_seed": 11,
            "created_at": "2026-09-15T10:00:00+00:00",
        },
        {
            "source_link": "https://example.com/demo/game-switch",
            "title": "スイッチ向け新作が話題。プレイ前に確認したい3つのポイント",
            "body": "新作タイトルの情報が広がっています。\n\n難易度、プレイ時間、周辺グッズの有無など、始める前に見たい点をまとめました。",
            "badge": "ゲーム",
            "keyword": "Nintendo Switch 新作",
            "has_product_links": False,
            "image_seed": 22,
            "created_at": "2026-09-14T10:00:00+00:00",
        },
        {
            "source_link": "https://example.com/demo/gadget-earbuds",
            "title": "軽量ワイヤレスイヤホンの選び方。通勤・学習向けチェックリスト",
            "body": "装着感、バッテリー、ノイズキャンセリング。\n\n失敗しにくい比較観点をわかりやすく紹介します。",
            "badge": "ガジェット",
            "keyword": "ワイヤレスイヤホン",
            "has_product_links": False,
            "image_seed": 33,
            "created_at": "2026-09-13T10:00:00+00:00",
        },
        {
            "source_link": "https://example.com/demo/goods-acrylic",
            "title": "人気キャラのアクリルスタンド。探すときの状態チェック",
            "body": "公式グッズの中でも人気が高いアイテムです。\n\n傷の有無や箱あり・箱なしなど、見るべき点を整理しました。",
            "badge": "トレンドグッズ",
            "keyword": "アクリルスタンド",
            "has_product_links": False,
            "image_seed": 44,
            "created_at": "2026-09-12T10:00:00+00:00",
        },
        {
            "source_link": "https://example.com/demo/anime-bd",
            "title": "完結アニメのBlu-ray。揃える前に見るべきスペック",
            "body": "収録話数、特典、音声仕様。\n\n購入前に確認したいポイントをコンパクトにまとめました。",
            "badge": "アニメ",
            "keyword": "アニメ Blu-ray",
            "has_product_links": False,
            "image_seed": 55,
            "created_at": "2026-09-11T10:00:00+00:00",
        },
    ]

    entries = load_entries()
    existing_ids = {e.article_id for e in entries}
    for demo in demos:
        if len(entries) >= min_total:
            break
        aid = article_id_from_link(str(demo["source_link"]))
        if aid in existing_ids:
            continue
        publish_article(
            source_link=str(demo["source_link"]),
            title=str(demo["title"]),
            body=str(demo["body"]),
            has_product_links=bool(demo["has_product_links"]),
            keyword=str(demo["keyword"]),
            badge=str(demo["badge"]),
            image_seed=int(demo["image_seed"]),
            created_at=str(demo["created_at"]),
            links={},
        )
        entries = load_entries()
        existing_ids = {e.article_id for e in entries}

    # 既存の「商品」バッジを置換して再描画
    changed = False
    refreshed: list[ArticleEntry] = []
    for e in entries:
        new_badge = _badge_for(has_product_links=e.has_product_links, badge=e.badge)
        if new_badge != e.badge or not e.image_seed:
            changed = True
            refreshed.append(
                ArticleEntry(
                    article_id=e.article_id,
                    filename=e.filename,
                    title=e.title,
                    excerpt=e.excerpt,
                    source_link=e.source_link,
                    created_at=e.created_at,
                    has_product_links=e.has_product_links,
                    keyword=e.keyword,
                    badge=new_badge,
                    image_seed=e.image_seed or _image_seed_from_id(e.article_id),
                    links=e.links,
                )
            )
        else:
            refreshed.append(e)
    if changed:
        save_entries(refreshed)
    else:
        refreshed = entries
    (DOCS / "index.html").write_text(render_index_page(refreshed), encoding="utf-8")
    return refreshed
